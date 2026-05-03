# frozen_string_literal: true

class DashboardController < StaffController
  after_action :verify_authorized
  
  def show
    authorize :dashboard
    
    @hotels = Hotel.all
    @recent_reservations = Reservation.includes(:guest, :room).order(created_at: :desc).limit(10)
    @todays_check_ins = Reservation.where(check_in_date: Date.current, status: 'confirmed')
    @todays_check_outs = Reservation.where(check_out_date: Date.current, status: 'checked_in')
    @occupancy_rate = calculate_occupancy_rate
    
    # Room status counts for the Room Status section
    @available_rooms = Room.available.count
    @occupied_rooms = Room.occupied.count
    @cleaning_rooms = Room.cleaning.count
    @maintenance_rooms = Room.maintenance.count
    
    
    # Recent activity (last 5 activities for sidebar)
    @recent_activities = build_recent_activities
  end
  
  private
  
  def calculate_occupancy_rate
    # If we have a current hotel context, use its occupancy rate
    if current_hotel
      return current_hotel.occupancy_rate
    end
    
    # Otherwise calculate across all hotels in the account
    total_rooms = Room.count
    return 0 if total_rooms.zero?
    
    # Count rooms that have checked-in reservations for today
    # Note: Room is considered occupied on checkout date until actual checkout
    occupied_rooms = Room.joins(:reservations)
                         .where(reservations: { status: 'checked_in' })
                         .where("reservations.check_in_date <= ? AND reservations.check_out_date >= ?", Date.current, Date.current)
                         .distinct
                         .count
    
    (occupied_rooms.to_f / total_rooms * 100).round(1)
  end
  
  
  def build_recent_activities
    activities = []
    
    # Recent reservations
    recent_reservations = Reservation.includes(:guest)
                                    .order(created_at: :desc)
                                    .limit(3)
    
    recent_reservations.each do |reservation|
      activities << {
        type: 'reservation',
        icon_color: 'blue',
        title: "New reservation ##{reservation.confirmation_number}",
        subtitle: "#{reservation.guest&.full_name} - Room #{reservation.room&.room_number || 'TBA'}",
        time: time_ago_in_words(reservation.created_at)
      }
    end
    
    # Recent check-ins
    recent_checkins = Reservation.includes(:guest, :room)
                                 .where.not(actual_check_in: nil)
                                 .order(actual_check_in: :desc)
                                 .limit(2)
    
    recent_checkins.each do |reservation|
      activities << {
        type: 'checkin',
        icon_color: 'green',
        title: "Check-in completed Room #{reservation.room&.room_number}",
        subtitle: reservation.guest&.full_name,
        time: time_ago_in_words(reservation.actual_check_in)
      }
    end
    
    # Recent room status changes
    recently_cleaned = Room.where(status: 'cleaning')
                          .where.not(last_cleaned_at: nil)
                          .order(last_cleaned_at: :desc)
                          .limit(2)
    
    recently_cleaned.each do |room|
      activities << {
        type: 'housekeeping',
        icon_color: 'yellow',
        title: "Housekeeping requested Room #{room.room_number}",
        subtitle: "Floor #{room.floor}",
        time: room.last_cleaned_at ? time_ago_in_words(room.last_cleaned_at) : 'Recently'
      }
    end
    
    # Sort by recency and take top 5
    activities.sort_by { |a| a[:time] }.first(5)
  end
  
  def time_ago_in_words(time)
    return 'Just now' unless time
    
    seconds_ago = Time.current - time
    case seconds_ago
    when 0..59
      'Just now'
    when 60..3599
      "#{(seconds_ago / 60).round} minutes ago"
    when 3600..86399
      "#{(seconds_ago / 3600).round} hours ago"
    else
      "#{(seconds_ago / 86400).round} days ago"
    end
  end
end