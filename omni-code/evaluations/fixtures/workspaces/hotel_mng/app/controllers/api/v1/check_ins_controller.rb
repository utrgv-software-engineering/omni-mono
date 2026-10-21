class Api::V1::CheckInsController < Api::V1::BaseController
  before_action :set_reservation
  
  # GET /api/v1/reservations/:reservation_id/check_in/available_rooms
  def available_rooms
    authorize @reservation, :check_in?
    
    # Find available rooms for this room type
    available_rooms = @reservation.hotel.rooms
                                  .where(room_type: @reservation.room_type, status: 'available')
                                  .order(:room_number)
    
    render json: {
      data: RoomBlueprint.render_as_hash(available_rooms)
    }
  end
  
  # POST /api/v1/reservations/:reservation_id/check_in
  def create
    authorize @reservation, :check_in?
    
    # Validate reservation can be checked in
    unless @reservation.status == 'confirmed'
      render json: {
        error: 'Conflict',
        message: 'Only confirmed reservations can be checked in'
      }, status: :conflict
      return
    end
    
    # Check if check-in date is valid
    if @reservation.check_in_date > Date.current
      render json: {
        error: 'Conflict',
        message: "Check-in date is in the future: #{@reservation.check_in_date}"
      }, status: :conflict
      return
    end
    
    # Assign room if provided
    if params[:room_id].present?
      room = @reservation.hotel.rooms.find_by(id: params[:room_id])
      unless room
        render_not_found('Room')
        return
      end
      
      unless room.available?
        render json: {
          error: 'Conflict',
          message: 'Room is not available'
        }, status: :conflict
        return
      end
      
      # Verify room type matches
      unless room.room_type_id == @reservation.room_type_id
        render json: {
          error: 'Conflict',
          message: 'Room type does not match reservation'
        }, status: :conflict
        return
      end
      
      @reservation.room = room
    elsif @reservation.room_id.nil?
      render json: {
        error: 'Bad Request',
        message: 'Room assignment required for check-in'
      }, status: :bad_request
      return
    end
    
    # Update reservation status and check-in time
    @reservation.status = 'checked_in'
    @reservation.actual_check_in = Time.current
    
    # Update room status if assigned
    if @reservation.room
      @reservation.room.status = 'occupied'
      @reservation.room.save!
    end
    
    if @reservation.save
      render json: {
        data: ReservationBlueprint.render_as_hash(@reservation),
        message: 'Guest successfully checked in'
      }, status: :ok
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@reservation))
    end
  end
  
  private
  
  def set_reservation
    @reservation = Reservation.find(params[:reservation_id])
    
    # Ensure reservation belongs to current account through hotel
    unless @current_account.hotels.exists?(id: @reservation.hotel_id)
      render_not_found('Reservation')
    end
  rescue ActiveRecord::RecordNotFound
    render_not_found('Reservation')
  end
end