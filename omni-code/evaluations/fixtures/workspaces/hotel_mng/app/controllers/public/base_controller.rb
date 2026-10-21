# frozen_string_literal: true

module Public
  class BaseController < ApplicationController
    include PublicUrlHelper
    helper PublicUrlHelper
    
    # Skip authentication for public pages
    # No need to skip authenticate_user! since it's not defined in ApplicationController
    
    # Skip tenant setting for public pages
    skip_before_action :set_tenant
    skip_before_action :set_current_account_for_user
    
    private
    
    def set_hotel_from_subdomain
      # Get hotel from middleware if available
      @hotel = request.env['hotel.current']
      
      # Fallback to subdomain if middleware didn't set it
      if @hotel.nil? && request.subdomain.present? && request.subdomain != 'www'
        @hotel = Hotel.find_by(subdomain: request.subdomain)
      end
      
      # Legacy support for direct hotel_id parameter
      if @hotel.nil? && params[:hotel_id].present?
        @hotel = Hotel.find(params[:hotel_id])
      end
      
      unless @hotel
        flash[:alert] = "Hotel not found. Please select a hotel."
        redirect_to root_path and return
      end
    end
  end
end