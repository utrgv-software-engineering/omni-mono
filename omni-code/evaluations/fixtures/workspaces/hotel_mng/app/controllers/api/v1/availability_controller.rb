class Api::V1::AvailabilityController < Api::V1::BaseController
  before_action :set_hotel
  before_action :validate_dates
  
  # GET /api/v1/hotels/:hotel_id/availability
  def index
    authorize @current_hotel, :show?
    
    check_in = Date.parse(params[:check_in_date])
    check_out = Date.parse(params[:check_out_date])
    total_guests = (params[:adults] || 1).to_i + (params[:children] || 0).to_i
    nights = (check_out - check_in).to_i
    
    # Get room types that can accommodate the guests
    room_types = @current_hotel.room_types
                               .where('max_occupancy >= ?', total_guests)
                               .order(:base_price)
    
    # Build availability data for each room type
    availability_data = room_types.map do |room_type|
      available_count = room_type.available_rooms_count(check_in, check_out)
      next if available_count == 0
      
      {
        room_type: RoomTypeBlueprint.render_as_hash(room_type),
        available_count: available_count,
        total_price: room_type.calculate_price(nights: nights, guests: total_guests),
        nights: nights
      }
    end.compact
    
    render json: { data: availability_data }
  end
  
  private
  
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end
  
  def validate_dates
    unless params[:check_in_date].present? && params[:check_out_date].present?
      render json: {
        error: 'Bad Request',
        message: 'check_in_date and check_out_date are required',
        status: 400
      }, status: :bad_request
      return false
    end
    
    check_in = Date.parse(params[:check_in_date]) rescue nil
    check_out = Date.parse(params[:check_out_date]) rescue nil
    
    if check_in.nil? || check_out.nil?
      render json: {
        error: 'Bad Request',
        message: 'Invalid date format. Use YYYY-MM-DD',
        status: 400
      }, status: :bad_request
      return false
    end
    
    if check_out <= check_in
      render json: {
        error: 'Bad Request',
        message: 'Check-out date must be after check-in date',
        status: 400
      }, status: :bad_request
      return false
    end
  rescue ArgumentError
    render json: {
      error: 'Bad Request',
      message: 'Invalid date format. Use YYYY-MM-DD',
      status: 400
    }, status: :bad_request
  end
end