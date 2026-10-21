# frozen_string_literal: true

class CheckInsController < StaffController
  before_action :set_reservation
  
  def new
    authorize @reservation, :check_in?
    
    # Find available rooms for this room type
    @available_rooms = @reservation.hotel.rooms
                                    .where(room_type: @reservation.room_type, status: 'available')
                                    .order(:room_number)
  end
  
  def create
    authorize @reservation, :check_in?
    
    # Assign room if selected
    if params[:room_id].present?
      room = Room.find(params[:room_id])
      @reservation.room = room
    end
    
    # Update reservation status and check-in time
    @reservation.status = 'checked_in'
    @reservation.actual_check_in = Time.current
    
    # Update room status if assigned
    if @reservation.room
      @reservation.room.status = 'occupied'
      @reservation.room.save
    end
    
    if @reservation.save
      redirect_to @reservation, notice: 'Guest successfully checked in.'
    else
      @available_rooms = @reservation.hotel.rooms
                                      .where(room_type: @reservation.room_type, status: 'available')
                                      .order(:room_number)
      render :new, status: :unprocessable_content
    end
  end
  
  private
  
  def set_reservation
    @reservation = Reservation.find(params[:reservation_id])
  end
end