class Api::V1::RoomTypesController < Api::V1::BaseController
  before_action :set_hotel
  before_action :set_room_type, only: [:show, :update, :destroy]
  
  # GET /api/v1/hotels/:hotel_id/room_types
  def index
    authorize RoomType, :index?
    
    room_types = @current_hotel.room_types.order(:name)
    
    @pagy, @room_types = pagy(room_types, limit: per_page, page: current_page)
    
    render json: {
      data: RoomTypeBlueprint.render_as_hash(@room_types),
      meta: pagination_meta(@pagy)
    }
  end
  
  # GET /api/v1/hotels/:hotel_id/room_types/:id
  def show
    authorize @room_type
    
    render json: {
      data: RoomTypeBlueprint.render_as_hash(@room_type)
    }
  end
  
  # POST /api/v1/hotels/:hotel_id/room_types
  def create
    @room_type = @current_hotel.room_types.build(room_type_params)
    authorize @room_type
    
    if @room_type.save
      render json: {
        data: RoomTypeBlueprint.render_as_hash(@room_type)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@room_type))
    end
  end
  
  # PATCH/PUT /api/v1/hotels/:hotel_id/room_types/:id
  def update
    authorize @room_type
    
    if @room_type.update(room_type_params)
      render json: {
        data: RoomTypeBlueprint.render_as_hash(@room_type)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@room_type))
    end
  end
  
  # DELETE /api/v1/hotels/:hotel_id/room_types/:id
  def destroy
    authorize @room_type
    
    # Check if room type has any rooms
    if @room_type.rooms.exists?
      render json: {
        error: 'Conflict',
        message: 'Cannot delete room type with existing rooms'
      }, status: :conflict
    else
      @room_type.destroy
      head :no_content
    end
  end
  
  private
  
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end
  
  def set_room_type
    @room_type = @current_hotel.room_types.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Room Type')
  end
  
  def room_type_params
    params.permit(
      :name, :code, :description, :base_price, :max_occupancy,
      :standard_occupancy, :extra_person_charge, :size_sqm
    )
  end
end