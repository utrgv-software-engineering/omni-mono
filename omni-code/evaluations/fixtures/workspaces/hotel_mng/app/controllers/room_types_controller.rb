# frozen_string_literal: true

class RoomTypesController < StaffController
  before_action :set_hotel
  before_action :set_room_type, only: [:show, :edit, :update, :destroy]
  
  def index
    @room_types = policy_scope(@hotel.room_types)
    authorize RoomType
  end
  
  def show
    authorize @room_type
  end
  
  def new
    @room_type = @hotel.room_types.build
    authorize @room_type
  end
  
  def create
    @room_type = @hotel.room_types.build(room_type_params)
    authorize @room_type
    
    if @room_type.save
      redirect_to hotel_room_type_path(@hotel, @room_type), notice: 'Room type was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @room_type
  end
  
  def update
    authorize @room_type
    if @room_type.update(room_type_params)
      redirect_to hotel_room_type_path(@hotel, @room_type), notice: 'Room type was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @room_type
    @room_type.destroy
    redirect_to hotel_room_types_path(@hotel), notice: 'Room type was successfully destroyed.'
  end
  
  private
  
  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end
  
  def set_room_type
    @room_type = @hotel.room_types.find(params[:id])
  end
  
  def room_type_params
    params.require(:room_type).permit(
      :name, :code, :description, :base_price, :max_occupancy,
      :standard_occupancy, :extra_person_charge, :size_sqm
    )
  end
end