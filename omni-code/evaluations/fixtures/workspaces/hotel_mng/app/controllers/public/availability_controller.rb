# frozen_string_literal: true

module Public
  class AvailabilityController < BaseController
    before_action :set_hotel_from_subdomain
    
    def index
      @check_in_date = params[:check_in_date] ? Date.parse(params[:check_in_date]) : Date.current
      @check_out_date = params[:check_out_date] ? Date.parse(params[:check_out_date]) : Date.current + 1
      @guests = params[:guests]&.to_i || 1
      
      # Find available room types
      @available_room_types = find_available_room_types
    end
    
    private
    
    def find_available_room_types
      # Get all room types for the hotel that can accommodate the guests
      room_types = @hotel.room_types.where("max_occupancy >= ?", @guests).order(created_at: :desc)
      
      # Check availability for each room type
      available_room_types = []
      
      room_types.each do |room_type|
        # Use the room_type's method to check availability
        available_count = room_type.available_rooms_count(@check_in_date, @check_out_date)
        
        if available_count > 0
          # Add a singleton method to store the available count for display
          room_type.define_singleton_method(:available_count) { available_count }
          available_room_types << room_type
        end
      end
      
      available_room_types
    end
  end
end