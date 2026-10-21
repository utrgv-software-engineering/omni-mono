class StaffController < ApplicationController
  layout 'staff'
  
  before_action :authenticate_user!
  before_action :require_account!
  before_action :load_sidebar_data
  # These will be set by child controllers as needed
  # after_action :verify_authorized
  # after_action :verify_policy_scoped
  
  private
  
  def pundit_user
    current_account_user
  end
  
  def load_sidebar_data
    return unless current_hotel
    
    # Load data for sidebar badges
    @todays_arrivals_count = Reservation.where(
      hotel_id: current_hotel.id,
      check_in_date: Date.current,
      status: ['confirmed', 'pending']
    ).count
    
    @todays_departures_count = Reservation.where(
      hotel_id: current_hotel.id,
      check_out_date: Date.current,
      status: 'checked_in'
    ).count
    
    @rooms_need_cleaning = Room.where(
      hotel_id: current_hotel.id,
      status: 'cleaning'
    ).count
    
    # Room inventory counts for sidebar
    @available_rooms_count = Room.where(
      hotel_id: current_hotel.id,
      status: 'available'
    ).count
    
    @occupied_rooms_count = Room.where(
      hotel_id: current_hotel.id,
      status: 'occupied'
    ).count
  end
end