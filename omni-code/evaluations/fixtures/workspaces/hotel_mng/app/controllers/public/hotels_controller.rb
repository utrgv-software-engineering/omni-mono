# frozen_string_literal: true

module Public
  class HotelsController < BaseController
    before_action :set_hotel_from_subdomain
    
    def show
      # Public hotel page with information
      @room_types = @hotel.room_types.includes(:rooms, :amenities)
      @hotel_amenities = @hotel.available_amenities.includes(:hotel_amenities)
      @hotel_policies = @hotel.hotel_policies.where(is_active: true).order(:sort_order)
    end
  end
end