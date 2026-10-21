# frozen_string_literal: true

class RoomsController < StaffController
  before_action :set_hotel
  before_action :set_room, only: [:show, :edit, :update, :destroy]
  
  def index
    @rooms = policy_scope(@hotel.rooms.includes(:room_type))
    authorize Room
  end
  
  def show
    authorize @room
  end
  
  def new
    @room = @hotel.rooms.build
    authorize @room
  end
  
  def create
    @room = @hotel.rooms.build(room_params)
    authorize @room
    
    if @room.save
      redirect_to hotel_room_path(@hotel, @room), notice: 'Room was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @room
  end
  
  def update
    authorize @room
    
    # Check if user is housekeeping and restrict params
    permitted_params = if current_account_user.has_role?(:housekeeping) && !current_account_user.has_role?(:hotel_manager)
      params.require(:room).permit(:status, :last_cleaned_at)
    else
      room_params
    end
    
    if @room.update(permitted_params)
      redirect_to hotel_room_path(@hotel, @room), notice: 'Room was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @room
    @room.destroy
    redirect_to hotel_rooms_path(@hotel), notice: 'Room was successfully destroyed.'
  end
  
  private
  
  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end
  
  def set_room
    @room = @hotel.rooms.find(params[:id])
  end
  
  def room_params
    params.require(:room).permit(
      :room_type_id, :room_number, :floor, :status, :is_smoking, :notes, :last_cleaned_at
    )
  end
end