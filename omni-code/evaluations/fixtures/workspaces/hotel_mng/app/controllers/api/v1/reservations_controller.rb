class Api::V1::ReservationsController < Api::V1::BaseController
  before_action :set_hotel, only: [:index, :create]
  before_action :set_reservation, only: [:show, :update, :destroy]
  
  # GET /api/v1/reservations/lookup?confirmation_number=XXX
  # GET /api/v1/reservations/lookup?email=XXX
  #
  # Secure lookup methods - require exact match on confirmation number OR email.
  # For customer-facing agents, these are preferred over list_hotel_reservations
  # which allows fuzzy/partial search.
  def lookup
    authorize Reservation, :index?

    unless params[:confirmation_number].present? || params[:email].present?
      render json: {
        error: 'Bad Request',
        message: 'Either confirmation_number or email parameter is required',
        status: 400
      }, status: :bad_request
      return
    end

    # Find reservation within the current account's hotels
    scope = Reservation.joins(:hotel)
                       .where(hotels: { account_id: @current_account.id })

    if params[:confirmation_number].present?
      # Lookup by exact confirmation number
      reservations = scope.where(confirmation_number: params[:confirmation_number])
    else
      # Lookup by exact email (case-insensitive)
      reservations = scope.joins(:guest)
                          .where("LOWER(guests.email) = LOWER(?)", params[:email])
    end

    if reservations.any?
      # Return single reservation for confirmation lookup, array for email lookup
      if params[:confirmation_number].present?
        authorize reservations.first, :show?
        render json: {
          data: ReservationBlueprint.render_as_hash(reservations.first)
        }
      else
        reservations.each { |r| authorize r, :show? }
        render json: {
          data: reservations.map { |r| ReservationBlueprint.render_as_hash(r) }
        }
      end
    else
      render_not_found('Reservation')
    end
  end
  
  # GET /api/v1/hotels/:hotel_id/reservations
  def index
    authorize Reservation, :index?
    
    reservations = @current_hotel.reservations.includes(:guest, :room, :room_type)
    reservations = filter_reservations(reservations)
    
    render_paginated(reservations, ReservationBlueprint)
  end
  
  # POST /api/v1/hotels/:hotel_id/reservations
  def create
    authorize Reservation, :create?
    
    # Find or create guest
    guest = find_or_create_guest
    return if performed? # Check if error was rendered
    
    # Build reservation
    @reservation = @current_hotel.reservations.build(reservation_params)
    @reservation.account = @current_account
    @reservation.guest = guest
    @reservation.status = 'confirmed'
    @reservation.payment_status = 'pending'
    
    # Generate confirmation number
    @reservation.confirmation_number = generate_confirmation_number
    
    # Calculate total amount
    nights = (@reservation.check_out_date - @reservation.check_in_date).to_i
    room_type = @current_hotel.room_types.find(params[:room_type_id])
    total_guests = @reservation.adults + @reservation.children
    @reservation.total_amount = room_type.calculate_price(nights: nights, guests: total_guests)
    
    if @reservation.save
      render json: {
        data: ReservationBlueprint.render_as_hash(@reservation)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@reservation))
    end
  end
  
  # GET /api/v1/reservations/:id
  def show
    authorize @reservation, :show?
    
    render json: {
      data: ReservationBlueprint.render_as_hash(@reservation)
    }
  end
  
  # PATCH /api/v1/reservations/:id
  def update
    authorize @reservation, :update?
    
    # Don't allow certain status changes via API
    if params[:status].present? && !%w[confirmed cancelled].include?(params[:status])
      render json: {
        error: 'Bad Request',
        message: 'Invalid status. Only confirmed or cancelled allowed',
        status: 400
      }, status: :bad_request
      return
    end
    
    # Clear room assignment if dates or room type change
    if params[:check_in_date].present? || params[:check_out_date].present? || params[:room_type_id].present?
      @reservation.room_id = nil unless params[:room_id].present?
    end
    
    if @reservation.update(update_reservation_params)
      render json: {
        data: ReservationBlueprint.render_as_hash(@reservation)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@reservation))
    end
  end
  
  # DELETE /api/v1/reservations/:id
  def destroy
    authorize @reservation, :destroy?
    
    # Only allow deleting cancelled or no_show reservations
    unless %w[cancelled no_show].include?(@reservation.status)
      render json: {
        error: 'Conflict',
        message: 'Only cancelled or no_show reservations can be deleted'
      }, status: :conflict
      return
    end
    
    @reservation.destroy
    head :no_content
  end
  
  
  private
  
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end
  
  def set_reservation
    @reservation = Reservation.find(params[:id])
    # Ensure reservation belongs to current account through hotel
    unless @current_account.hotels.exists?(id: @reservation.hotel_id)
      render_not_found('Reservation')
    end
  rescue ActiveRecord::RecordNotFound
    render_not_found('Reservation')
  end
  
  def find_or_create_guest
    guest_params = params.require(:guest).permit(
      :id, :first_name, :last_name, :email, :phone, 
      :address, :city, :state, :country, :postal_code
    )
    
    # If guest ID provided, find existing guest
    if guest_params[:id].present?
      guest = Guest.find_by(id: guest_params[:id])
      unless guest
        render json: {
          error: 'Not Found',
          message: 'Guest not found',
          status: 404
        }, status: :not_found
        return nil
      end
      return guest
    end
    
    # Find existing guest by email or create new
    guest = Guest.find_by(email: guest_params[:email])
    
    if guest
      # Update guest info if provided
      guest.update(guest_params.except(:id))
    else
      # Create new guest
      guest = Guest.create(guest_params.except(:id))
      unless guest.persisted?
        render json: {
          error: 'Validation Failed',
          message: 'Could not create guest',
          errors: format_validation_errors(guest.errors),
          status: 422
        }, status: :unprocessable_content
        return nil
      end
    end
    
    guest
  end
  
  def filter_reservations(scope)
    scope = scope.where(status: params[:status]) if params[:status].present?
    scope = scope.where(check_in_date: params[:check_in_date]) if params[:check_in_date].present?

    # Search by guest name or email (partial match) - useful for staff/admin dashboards
    # NOTE: This is a powerful search capability. Customer-facing agents should use
    # lookup_reservation (by confirmation number) instead for privacy reasons.
    if params[:guest_search].present?
      scope = scope.joins(:guest).where(
        "guests.first_name ILIKE :search OR guests.last_name ILIKE :search OR guests.email ILIKE :search",
        search: "%#{params[:guest_search]}%"
      )
    end

    scope.order(check_in_date: :desc)
  end
  
  def generate_confirmation_number
    date_part = Date.today.strftime('%Y%m%d')
    random_part = SecureRandom.hex(3).upcase
    "#{date_part}#{random_part}"
  end
  
  def reservation_params
    params.permit(
      :room_type_id, :check_in_date, :check_out_date,
      :adults, :children, :infants, :special_requests, :source
    ).tap do |p|
      p[:children] ||= 0
      p[:infants] ||= 0
      p[:source] ||= 'website'
      p[:currency] = 'USD'
    end
  end
  
  def update_reservation_params
    params.permit(
      :check_in_date, :check_out_date, :room_id, :room_type_id,
      :adults, :children, :infants, :status, :special_requests, :internal_notes
    )
  end
end