# frozen_string_literal: true

class ReservationsController < StaffController
  before_action :set_reservation, only: [:show, :edit, :update, :destroy]
  after_action :verify_authorized, except: :index
  after_action :verify_policy_scoped, only: :index
  
  def index
    @reservations = policy_scope(Reservation.includes(:guest, :hotel, :room, :room_type))
    authorize Reservation
    
    # Special filters
    case params[:filter]
    when 'arrivals'
      @reservations = @reservations.where(check_in_date: Date.current, status: ['confirmed', 'pending'])
      @page_subtitle = "Today's Arrivals"
    when 'departures'
      @reservations = @reservations.where(check_out_date: Date.current, status: 'checked_in')
      @page_subtitle = "Today's Departures"
    when 'in_house'
      @reservations = @reservations.where(status: 'checked_in')
      @page_subtitle = "In House Guests"
    end
    
    # Filter by status
    if params[:status].present?
      @reservations = @reservations.where(status: params[:status])
    end
    
    # Filter by date range
    if params[:start_date].present? && params[:end_date].present?
      start_date = Date.parse(params[:start_date])
      end_date = Date.parse(params[:end_date])
      @reservations = @reservations.where("check_in_date <= ? AND check_out_date >= ?", end_date, start_date)
    elsif params[:date].present?
      date = Date.parse(params[:date])
      @reservations = @reservations.where("check_in_date <= ? AND check_out_date >= ?", date, date)
    end
    
    # Search by guest name or confirmation number
    if params[:search].present?
      search_term = "%#{params[:search]}%"
      @reservations = @reservations.joins(:guest)
                                   .where("reservations.confirmation_number ILIKE ? OR guests.first_name ILIKE ? OR guests.last_name ILIKE ? OR guests.email ILIKE ?", 
                                          search_term, search_term, search_term, search_term)
    end
    
    @pagy, @reservations = pagy(@reservations.order(check_in_date: :desc, created_at: :desc))
  end
  
  def show
    authorize @reservation
  end
  
  def new
    @reservation = Reservation.new
    @reservation.hotel_id = params[:hotel_id] if params[:hotel_id].present?
    authorize @reservation
    
    @hotels = Hotel.all
    @guests = Guest.order(:last_name, :first_name)
    @room_types = params[:hotel_id] ? Hotel.find(params[:hotel_id]).room_types : RoomType.none
  end
  
  def create
    @reservation = Reservation.new(reservation_params)
    @reservation.confirmation_number = generate_confirmation_number
    authorize @reservation
    
    if @reservation.save
      redirect_to @reservation, notice: 'Reservation was successfully created.'
    else
      @hotels = Hotel.all
      @guests = Guest.order(:last_name, :first_name)
      @room_types = @reservation.hotel&.room_types || RoomType.none
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @reservation
    @hotels = Hotel.all
    @guests = Guest.order(:last_name, :first_name)
    @room_types = @reservation.hotel.room_types
    @rooms = @reservation.room_type.rooms.where(hotel: @reservation.hotel)
  end
  
  def update
    authorize @reservation
    
    if @reservation.update(reservation_params)
      redirect_to @reservation, notice: 'Reservation was successfully updated.'
    else
      @hotels = Hotel.all
      @guests = Guest.order(:last_name, :first_name)
      @room_types = @reservation.hotel.room_types
      @rooms = @reservation.room_type.rooms.where(hotel: @reservation.hotel)
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @reservation
    @reservation.destroy
    redirect_to reservations_path, notice: 'Reservation was successfully cancelled.'
  end
  
  private
  
  def set_reservation
    @reservation = Reservation.find(params[:id])
  end
  
  def reservation_params
    params.require(:reservation).permit(
      :hotel_id, :guest_id, :room_id, :room_type_id, :check_in_date, :check_out_date,
      :status, :adults, :children, :infants, :total_amount, :paid_amount,
      :payment_status, :special_requests, :internal_notes, :source
    )
  end
  
  def generate_confirmation_number
    loop do
      number = "#{Date.current.strftime('%Y%m%d')}#{SecureRandom.hex(3).upcase}"
      break number unless Reservation.exists?(confirmation_number: number)
    end
  end
end