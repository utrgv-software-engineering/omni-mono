class Api::V1::RoomsController < Api::V1::BaseController
  before_action :set_hotel
  before_action :set_room, only: [:show, :update, :destroy, :update_status]
  
  # GET /api/v1/hotels/:hotel_id/rooms
  def index
    authorize Room, :index?
    
    rooms = @current_hotel.rooms.includes(:room_type)
    rooms = filter_rooms(rooms)
    
    @pagy, @rooms = pagy(rooms, limit: per_page, page: current_page)
    
    render json: {
      data: RoomBlueprint.render_as_hash(@rooms),
      meta: pagination_meta(@pagy)
    }
  end
  
  # GET /api/v1/hotels/:hotel_id/rooms/:id
  def show
    authorize @room
    
    render json: {
      data: RoomBlueprint.render_as_hash(@room)
    }
  end
  
  # POST /api/v1/hotels/:hotel_id/rooms
  def create
    @room = @current_hotel.rooms.build(room_params)
    authorize @room
    
    if @room.save
      render json: {
        data: RoomBlueprint.render_as_hash(@room)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@room))
    end
  end
  
  # PATCH/PUT /api/v1/hotels/:hotel_id/rooms/:id
  def update
    authorize @room
    
    # Check if user is housekeeping and restrict params
    permitted_params = if @current_account_user.has_role?(:housekeeping) && !@current_account_user.has_any_role?(:owner, :admin, :hotel_manager)
      params.permit(:status, :last_cleaned_at)
    else
      room_params
    end
    
    if @room.update(permitted_params)
      render json: {
        data: RoomBlueprint.render_as_hash(@room)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@room))
    end
  end
  
  # DELETE /api/v1/hotels/:hotel_id/rooms/:id
  def destroy
    authorize @room
    
    # Check if room has any active reservations
    if @room.reservations.where(status: ['confirmed', 'checked_in']).exists?
      render json: {
        error: 'Conflict',
        message: 'Cannot delete room with active reservations'
      }, status: :conflict
    else
      @room.destroy
      head :no_content
    end
  end
  
  # PATCH /api/v1/hotels/:hotel_id/rooms/:id/status
  def update_status
    authorize @room, :update?
    
    unless Room::STATUSES.include?(params[:status])
      render json: {
        error: 'Bad Request',
        message: "Invalid status. Must be one of: #{Room::STATUSES.join(', ')}"
      }, status: :bad_request
      return
    end
    
    @room.status = params[:status]
    @room.last_cleaned_at = Time.current if params[:status] == 'available'
    
    if @room.save
      render json: {
        data: RoomBlueprint.render_as_hash(@room)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@room))
    end
  end
  
  private
  
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end
  
  def set_room
    @room = @current_hotel.rooms.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Room')
  end
  
  def filter_rooms(scope)
    scope = scope.where(status: params[:status]) if params[:status].present?
    scope = scope.where(floor: params[:floor]) if params[:floor].present?
    scope = scope.where(room_type_id: params[:room_type_id]) if params[:room_type_id].present?
    scope.order(:room_number)
  end
  
  def room_params
    params.permit(
      :room_type_id, :room_number, :floor, :status, :is_smoking, :notes, :last_cleaned_at
    )
  end
end