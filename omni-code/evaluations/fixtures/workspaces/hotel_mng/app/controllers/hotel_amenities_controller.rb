# frozen_string_literal: true

class HotelAmenitiesController < StaffController
  before_action :set_hotel
  before_action :set_hotel_amenity, only: [:show, :edit, :update, :destroy]
  
  def index
    @hotel_amenities = policy_scope(@hotel.hotel_amenities.includes(:amenity))
    authorize HotelAmenity
  end
  
  def show
    authorize @hotel_amenity
  end
  
  def new
    @hotel_amenity = @hotel.hotel_amenities.build
    authorize @hotel_amenity
    @available_amenities = Amenity.where.not(id: @hotel.hotel_amenities.pluck(:amenity_id))
                                  .where(category: ['hotel', 'bathroom', 'kitchen', 'entertainment'])
                                  .order(:category, :name)
  end
  
  def create
    @hotel_amenity = @hotel.hotel_amenities.build(hotel_amenity_params)
    authorize @hotel_amenity
    
    if @hotel_amenity.save
      redirect_to hotel_hotel_amenity_path(@hotel, @hotel_amenity), notice: 'Hotel amenity was successfully created.'
    else
      @available_amenities = Amenity.where.not(id: @hotel.hotel_amenities.pluck(:amenity_id))
                                    .where(category: ['hotel', 'bathroom', 'kitchen', 'entertainment'])
                                    .order(:category, :name)
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @hotel_amenity
  end
  
  def update
    authorize @hotel_amenity
    if @hotel_amenity.update(hotel_amenity_params)
      redirect_to hotel_hotel_amenity_path(@hotel, @hotel_amenity), notice: 'Hotel amenity was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @hotel_amenity
    @hotel_amenity.destroy
    redirect_to hotel_hotel_amenities_path(@hotel), notice: 'Hotel amenity was successfully removed.'
  end
  
  private
  
  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end
  
  def set_hotel_amenity
    @hotel_amenity = @hotel.hotel_amenities.find(params[:id])
  end
  
  def hotel_amenity_params
    params.require(:hotel_amenity).permit(:amenity_id, :is_available, :is_free, :additional_cost, :location, :operating_hours, :notes)
  end
end