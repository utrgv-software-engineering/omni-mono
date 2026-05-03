# frozen_string_literal: true

class RoomTypeAmenitiesController < StaffController
  before_action :set_hotel
  before_action :set_room_type
  before_action :set_room_type_amenity, only: [:destroy]
  
  def index
    @room_type_amenities = policy_scope(@room_type.room_type_amenities.includes(:amenity))
    authorize RoomTypeAmenity
    @available_amenities = Amenity.where.not(id: @room_type.room_type_amenities.pluck(:amenity_id))
                                  .where(category: ['room', 'bathroom', 'kitchen', 'entertainment'])
                                  .order(:category, :name)
  end
  
  def create
    @room_type_amenity = @room_type.room_type_amenities.build(room_type_amenity_params)
    authorize @room_type_amenity
    
    if @room_type_amenity.save
      redirect_to hotel_room_type_room_type_amenities_path(@hotel, @room_type), 
                  notice: 'Amenity was successfully added to room type.'
    else
      @room_type_amenities = @room_type.room_type_amenities.includes(:amenity)
      @available_amenities = Amenity.where.not(id: @room_type.room_type_amenities.pluck(:amenity_id))
                                    .where(category: ['room', 'bathroom', 'kitchen', 'entertainment'])
                                    .order(:category, :name)
      render :index, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @room_type_amenity
    @room_type_amenity.destroy
    redirect_to hotel_room_type_room_type_amenities_path(@hotel, @room_type), 
                notice: 'Amenity was successfully removed from room type.'
  end
  
  private
  
  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end
  
  def set_room_type
    @room_type = @hotel.room_types.find(params[:room_type_id])
  end
  
  def set_room_type_amenity
    @room_type_amenity = @room_type.room_type_amenities.find(params[:id])
  end
  
  def room_type_amenity_params
    params.require(:room_type_amenity).permit(:amenity_id, :quantity)
  end
end