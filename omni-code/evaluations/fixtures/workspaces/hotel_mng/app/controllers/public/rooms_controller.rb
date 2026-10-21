# frozen_string_literal: true

module Public
  class RoomsController < BaseController
    before_action :set_hotel_from_subdomain
    before_action :set_room_type, only: [:show]
    
    def index
      # Show all room types with availability for selected dates
      @room_types = @hotel.room_types.includes(:rooms)
      @check_in_date = params[:check_in_date] ? Date.parse(params[:check_in_date]) : Date.current
      @check_out_date = params[:check_out_date] ? Date.parse(params[:check_out_date]) : Date.current + 1
      @guests = params[:guests]&.to_i || 2
    end
    
    def show
      # Detailed view of a specific room type
      @check_in_date = params[:check_in_date] ? Date.parse(params[:check_in_date]) : Date.current
      @check_out_date = params[:check_out_date] ? Date.parse(params[:check_out_date]) : Date.current + 1
      @guests = params[:guests]&.to_i || 2
      
      # Check availability for this room type
      @available_count = @room_type.available_rooms_count(@check_in_date, @check_out_date)
      @nights = (@check_out_date - @check_in_date).to_i
      @total_price = @room_type.calculate_price(nights: @nights, guests: @guests)
      
      # Load room amenities and hotel policies
      @room_amenities = @room_type.amenities.includes(:room_type_amenities)
      @hotel_policies = @hotel.hotel_policies.where(is_active: true).order(:sort_order)
      @cancellation_policy = @hotel.cancellation_policies.find_by(is_default: true) || @hotel.cancellation_policies.first
    end
    
    private
    
    def set_room_type
      @room_type = @hotel.room_types.find(params[:id])
    end
  end
end