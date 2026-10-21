# Seed file for Blazer queries - common hotel management reports
# Run with: rails db:seed:blazer_queries

# Helper to create queries
def create_blazer_query(name, description, statement)
  Blazer::Query.create!(
    name: name,
    description: description,
    statement: statement,
    data_source: "main",
    status: "active"
  )
  puts "Created query: #{name}"
rescue ActiveRecord::RecordInvalid => e
  puts "Failed to create query '#{name}': #{e.message}"
end

puts "Creating Blazer sample queries..."

# Occupancy Reports
create_blazer_query(
  "Current Occupancy Rate by Hotel",
  "Shows current occupancy percentage for each hotel",
  <<~SQL
    SELECT 
      h.name as hotel_name,
      COUNT(DISTINCT r.id) as total_rooms,
      COUNT(DISTINCT CASE WHEN r.status = 'occupied' THEN r.id END) as occupied_rooms,
      ROUND(COUNT(DISTINCT CASE WHEN r.status = 'occupied' THEN r.id END) * 100.0 / 
            NULLIF(COUNT(DISTINCT r.id), 0), 2) as occupancy_rate
    FROM hotels h
    LEFT JOIN rooms r ON r.hotel_id = h.id
    WHERE h.account_id = {account_id}
    GROUP BY h.id, h.name
    ORDER BY occupancy_rate DESC
  SQL
)

create_blazer_query(
  "Monthly Occupancy Trends",
  "Historical occupancy rates by month",
  <<~SQL
    SELECT 
      DATE_TRUNC('month', res.check_in_date) as month,
      h.name as hotel_name,
      COUNT(DISTINCT res.id) as total_bookings,
      COUNT(DISTINCT res.room_id) as unique_rooms_booked,
      ROUND(AVG(res.check_out_date - res.check_in_date), 1) as avg_stay_length
    FROM reservations res
    JOIN hotels h ON h.id = res.hotel_id
    WHERE res.account_id = {account_id}
      AND res.status NOT IN ('cancelled', 'no_show')
      AND res.check_in_date >= {start_date}
      AND res.check_in_date <= {end_date}
    GROUP BY DATE_TRUNC('month', res.check_in_date), h.name
    ORDER BY month DESC, hotel_name
  SQL
)

# Revenue Reports
create_blazer_query(
  "Revenue by Hotel - Current Month",
  "Total revenue and paid amounts by hotel for current month",
  <<~SQL
    SELECT 
      h.name as hotel_name,
      COUNT(res.id) as total_reservations,
      SUM(res.total_amount) as total_revenue,
      SUM(res.paid_amount) as total_paid,
      SUM(res.total_amount - res.paid_amount) as outstanding_balance,
      ROUND(AVG(res.total_amount), 2) as avg_booking_value
    FROM reservations res
    JOIN hotels h ON h.id = res.hotel_id
    WHERE res.account_id = {account_id}
      AND res.status NOT IN ('cancelled')
      AND DATE_TRUNC('month', res.check_in_date) = DATE_TRUNC('month', CURRENT_DATE)
    GROUP BY h.id, h.name
    ORDER BY total_revenue DESC
  SQL
)

create_blazer_query(
  "Daily Revenue Breakdown",
  "Revenue by day with running total",
  <<~SQL
    WITH daily_revenue AS (
      SELECT 
        DATE(res.check_in_date) as date,
        SUM(res.total_amount) as daily_revenue,
        COUNT(res.id) as bookings_count
      FROM reservations res
      WHERE res.account_id = {account_id}
        AND res.status NOT IN ('cancelled')
        AND res.check_in_date >= {start_date}
        AND res.check_in_date <= {end_date}
      GROUP BY DATE(res.check_in_date)
    )
    SELECT 
      date,
      bookings_count,
      daily_revenue,
      SUM(daily_revenue) OVER (ORDER BY date) as running_total
    FROM daily_revenue
    ORDER BY date DESC
  SQL
)

# Guest Analytics
create_blazer_query(
  "Top Guests by Revenue",
  "Most valuable guests by total spending",
  <<~SQL
    SELECT 
      g.first_name || ' ' || g.last_name as guest_name,
      g.email,
      g.vip_status,
      COUNT(res.id) as total_bookings,
      SUM(res.total_amount) as lifetime_value,
      MAX(res.check_in_date) as last_visit,
      ROUND(AVG(res.total_amount), 2) as avg_booking_value
    FROM guests g
    JOIN reservations res ON res.guest_id = g.id
    WHERE g.account_id = {account_id}
      AND res.status NOT IN ('cancelled')
    GROUP BY g.id, g.first_name, g.last_name, g.email, g.vip_status
    HAVING COUNT(res.id) > 0
    ORDER BY lifetime_value DESC
    LIMIT 50
  SQL
)

create_blazer_query(
  "Guest Demographics",
  "Guest distribution by country and city",
  <<~SQL
    SELECT 
      COALESCE(g.country, 'Unknown') as country,
      COALESCE(g.city, 'Unknown') as city,
      COUNT(DISTINCT g.id) as guest_count,
      COUNT(DISTINCT res.id) as total_bookings,
      SUM(res.total_amount) as total_revenue
    FROM guests g
    LEFT JOIN reservations res ON res.guest_id = g.id AND res.status NOT IN ('cancelled')
    WHERE g.account_id = {account_id}
    GROUP BY g.country, g.city
    ORDER BY guest_count DESC
    LIMIT 100
  SQL
)

# Room Performance
create_blazer_query(
  "Room Type Performance",
  "Revenue and occupancy by room type",
  <<~SQL
    SELECT 
      rt.name as room_type,
      rt.base_price,
      COUNT(DISTINCT r.id) as total_rooms,
      COUNT(DISTINCT res.id) as total_bookings,
      SUM(res.total_amount) as total_revenue,
      ROUND(AVG(res.total_amount), 2) as avg_booking_value,
      ROUND(COUNT(DISTINCT res.id) * 100.0 / 
            NULLIF(COUNT(DISTINCT r.id) * 30, 0), 2) as utilization_rate
    FROM room_types rt
    LEFT JOIN rooms r ON r.room_type_id = rt.id
    LEFT JOIN reservations res ON res.room_type_id = rt.id 
      AND res.status NOT IN ('cancelled')
      AND res.check_in_date >= CURRENT_DATE - INTERVAL '30 days'
    WHERE rt.account_id = {account_id}
    GROUP BY rt.id, rt.name, rt.base_price
    ORDER BY total_revenue DESC
  SQL
)

create_blazer_query(
  "Rooms Requiring Maintenance",
  "List of rooms with maintenance status or recent issues",
  <<~SQL
    SELECT 
      h.name as hotel_name,
      r.room_number,
      r.floor,
      r.status,
      r.last_cleaned_at,
      COUNT(mr.id) as open_maintenance_requests,
      MAX(mr.created_at) as last_request_date
    FROM rooms r
    JOIN hotels h ON h.id = r.hotel_id
    LEFT JOIN maintenance_requests mr ON mr.room_id = r.id 
      AND mr.status IN ('open', 'in_progress')
    WHERE r.account_id = {account_id}
      AND (r.status = 'maintenance' OR COUNT(mr.id) > 0)
    GROUP BY h.name, r.id, r.room_number, r.floor, r.status, r.last_cleaned_at
    ORDER BY open_maintenance_requests DESC, last_request_date DESC
  SQL
)

# Housekeeping Reports
create_blazer_query(
  "Housekeeping Task Status",
  "Current status of housekeeping tasks",
  <<~SQL
    SELECT 
      h.name as hotel_name,
      ht.status,
      ht.priority,
      COUNT(ht.id) as task_count,
      COUNT(DISTINCT ht.room_id) as unique_rooms,
      AVG(EXTRACT(EPOCH FROM (ht.completed_at - ht.started_at))/3600) as avg_completion_hours
    FROM housekeeping_tasks ht
    JOIN hotels h ON h.id = ht.hotel_id
    WHERE ht.account_id = {account_id}
      AND ht.scheduled_date >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY h.name, ht.status, ht.priority
    ORDER BY h.name, 
      CASE ht.priority 
        WHEN 'urgent' THEN 1
        WHEN 'high' THEN 2
        WHEN 'normal' THEN 3
        WHEN 'low' THEN 4
      END,
      ht.status
  SQL
)

# Service Utilization
create_blazer_query(
  "Service Revenue Analysis",
  "Revenue from additional services",
  <<~SQL
    SELECT 
      s.name as service_name,
      s.category,
      s.price,
      COUNT(sb.id) as bookings_count,
      SUM(sb.total_price) as total_revenue,
      ROUND(AVG(sb.total_price), 2) as avg_booking_value
    FROM services s
    LEFT JOIN service_bookings sb ON sb.service_id = s.id
      AND sb.status = 'confirmed'
      AND sb.booking_date >= CURRENT_DATE - INTERVAL '30 days'
    WHERE s.account_id = {account_id}
      AND s.is_active = true
    GROUP BY s.id, s.name, s.category, s.price
    ORDER BY total_revenue DESC NULLS LAST
  SQL
)

# Cancellation Analysis
create_blazer_query(
  "Cancellation Rate Analysis",
  "Cancellation patterns and trends",
  <<~SQL
    SELECT 
      DATE_TRUNC('week', res.created_at) as week,
      COUNT(res.id) as total_bookings,
      COUNT(CASE WHEN res.status = 'cancelled' THEN 1 END) as cancellations,
      ROUND(COUNT(CASE WHEN res.status = 'cancelled' THEN 1 END) * 100.0 / 
            NULLIF(COUNT(res.id), 0), 2) as cancellation_rate
    FROM reservations res
    WHERE res.account_id = {account_id}
      AND res.created_at >= CURRENT_DATE - INTERVAL '90 days'
    GROUP BY DATE_TRUNC('week', res.created_at)
    ORDER BY week DESC
  SQL
)

# Operational Efficiency
create_blazer_query(
  "Check-in/Check-out Times Analysis",
  "Average processing times for check-ins and check-outs",
  <<~SQL
    SELECT 
      h.name as hotel_name,
      COUNT(CASE WHEN actual_check_in IS NOT NULL THEN 1 END) as total_checkins,
      AVG(EXTRACT(HOUR FROM actual_check_in::time)) as avg_checkin_hour,
      COUNT(CASE WHEN actual_check_out IS NOT NULL THEN 1 END) as total_checkouts,
      AVG(EXTRACT(HOUR FROM actual_check_out::time)) as avg_checkout_hour
    FROM reservations res
    JOIN hotels h ON h.id = res.hotel_id
    WHERE res.account_id = {account_id}
      AND res.check_in_date >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY h.id, h.name
    ORDER BY h.name
  SQL
)

# Future Bookings Forecast
create_blazer_query(
  "Future Bookings Calendar",
  "Upcoming reservations for the next 30 days",
  <<~SQL
    SELECT 
      res.check_in_date::date as date,
      h.name as hotel_name,
      COUNT(res.id) as arrivals,
      SUM(res.adults + res.children) as total_guests,
      SUM(res.total_amount) as expected_revenue
    FROM reservations res
    JOIN hotels h ON h.id = res.hotel_id
    WHERE res.account_id = {account_id}
      AND res.status IN ('pending', 'confirmed')
      AND res.check_in_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'
    GROUP BY res.check_in_date::date, h.name
    ORDER BY date, hotel_name
  SQL
)

# Staff Performance (for hotels with staff tracking)
create_blazer_query(
  "Housekeeping Staff Performance",
  "Efficiency metrics for housekeeping staff",
  <<~SQL
    SELECT 
      u.first_name || ' ' || u.last_name as staff_name,
      COUNT(ht.id) as tasks_completed,
      AVG(ht.inspection_score) as avg_inspection_score,
      AVG(EXTRACT(EPOCH FROM (ht.completed_at - ht.started_at))/60) as avg_completion_minutes,
      COUNT(CASE WHEN ht.priority = 'urgent' THEN 1 END) as urgent_tasks_handled
    FROM housekeeping_tasks ht
    JOIN users u ON u.id = ht.assigned_to_id
    JOIN account_users au ON au.user_id = u.id AND au.account_id = {account_id}
    WHERE ht.account_id = {account_id}
      AND ht.status = 'completed'
      AND ht.completed_at >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY u.id, u.first_name, u.last_name
    ORDER BY tasks_completed DESC
  SQL
)

puts "Blazer queries created successfully!"
puts "To view them, visit /blazer in your application"