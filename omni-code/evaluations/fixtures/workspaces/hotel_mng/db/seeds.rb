# Seed data for hotel management system

puts "🌱 Seeding database..."
puts "Clearing existing data..."
HousekeepingTask.destroy_all if defined?(HousekeepingTask)
MaintenanceRequest.destroy_all if defined?(MaintenanceRequest)
Reservation.destroy_all
Guest.destroy_all
Room.destroy_all
RoomTypeAmenity.destroy_all
HotelAmenity.destroy_all
Amenity.destroy_all
RoomType.destroy_all
HotelPolicy.destroy_all if defined?(HotelPolicy)
CancellationPolicy.destroy_all if defined?(CancellationPolicy)
Hotel.destroy_all
AccountUser.destroy_all
User.destroy_all
Account.destroy_all
Role.destroy_all

puts "Creating Priority 1 roles..."

# Create Priority 1 roles
owner_role = Role.create!(name: 'owner')
admin_role = Role.create!(name: 'admin')
hotel_manager_role = Role.create!(name: 'hotel_manager')
front_desk_role = Role.create!(name: 'front_desk')
housekeeping_role = Role.create!(name: 'housekeeping')

puts "Creating sample accounts..."

# Create sample account
account = Account.create!(
  name: "Demo Hotel Group",
  subdomain: "demo",
  address: "123 Hotel Street",
  city: "San Francisco",
  state: "CA",
  country: "USA",
  postal_code: "94102",
  phone: "415-555-0100",
  email: "info@demohotels.com",
  timezone: "Pacific Time (US & Canada)",
  currency: "USD",
  status: "active"
)

puts "Creating sample hotel..."

hotel = Hotel.create!(
  account: account,
  name: "Demo Hotel SF",
  subdomain: "sf",
  address: "456 Market Street",
  city: "San Francisco",
  state: "CA",
  country: "USA",
  postal_code: "94103",
  phone: "415-555-0200",
  email: "sf@demohotels.com",
  website: "https://demohotels.com/sf",
  description: "Modern hotel in downtown San Francisco",
  timezone: "Pacific Time (US & Canada)",
  star_rating: 4.0,
  total_rooms: 20
)

puts "Creating amenities..."

# Create common hotel amenities
hotel_amenities_data = [
  { name: "Free WiFi", category: "hotel", icon: "wifi", description: "High-speed wireless internet throughout the property" },
  { name: "Swimming Pool", category: "outdoor", icon: "person-swimming", description: "Outdoor heated swimming pool" },
  { name: "Fitness Center", category: "wellness", icon: "dumbbell", description: "24/7 gym with modern equipment" },
  { name: "Restaurant", category: "hotel", icon: "utensils", description: "On-site dining serving breakfast, lunch, and dinner" },
  { name: "Bar", category: "hotel", icon: "martini-glass", description: "Full-service bar with cocktails and local beers" },
  { name: "Free Parking", category: "hotel", icon: "square-parking", description: "Complimentary self-parking for guests" },
  { name: "Business Center", category: "business", icon: "briefcase", description: "Computer workstations and printing services" },
  { name: "Spa", category: "wellness", icon: "spa", description: "Full-service spa with massage and treatments" },
  { name: "Pet Friendly", category: "hotel", icon: "paw", description: "Pets welcome with additional fee" },
  { name: "Wheelchair Accessible", category: "accessibility", icon: "wheelchair", description: "Full wheelchair accessibility throughout property" },
  { name: "Concierge Service", category: "hotel", icon: "bell-concierge", description: "24/7 concierge assistance" },
  { name: "Laundry Service", category: "hotel", icon: "shirt", description: "Same-day laundry and dry cleaning" }
]

hotel_amenities = hotel_amenities_data.map do |amenity_data|
  Amenity.create!(
    account: account,
    name: amenity_data[:name],
    category: amenity_data[:category],
    icon: amenity_data[:icon],
    description: amenity_data[:description],
    is_countable: false
  )
end

# Create common room amenities
room_amenities_data = [
  { name: "Air Conditioning", category: "room", icon: "snowflake", description: "Individual climate control" },
  { name: "Flat Screen TV", category: "entertainment", icon: "tv", description: "Smart TV with cable channels", is_countable: true },
  { name: "Mini Bar", category: "room", icon: "wine-bottle", description: "Stocked mini refrigerator" },
  { name: "Safe", category: "room", icon: "vault", description: "In-room electronic safe" },
  { name: "Coffee Maker", category: "kitchen", icon: "mug-hot", description: "Coffee and tea making facilities" },
  { name: "Hair Dryer", category: "bathroom", icon: "wind", description: "Professional hair dryer" },
  { name: "Iron & Ironing Board", category: "room", icon: "shirt", description: "Iron with full-size ironing board" },
  { name: "Bathrobe", category: "bathroom", icon: "vest", description: "Plush terry bathrobes", is_countable: true },
  { name: "Queen Bed", category: "room", icon: "bed", description: "Queen size bed with premium mattress", is_countable: true },
  { name: "King Bed", category: "room", icon: "bed", description: "King size bed with premium mattress", is_countable: true },
  { name: "Work Desk", category: "room", icon: "desktop", description: "Large work desk with ergonomic chair" },
  { name: "Balcony", category: "room", icon: "building", description: "Private balcony with seating" },
  { name: "Ocean View", category: "room", icon: "water", description: "Direct ocean view from room" },
  { name: "Kitchenette", category: "kitchen", icon: "kitchen-set", description: "Small kitchen with basic appliances" },
  { name: "Sofa Bed", category: "room", icon: "couch", description: "Pull-out sofa bed", is_countable: true }
]

room_amenities = room_amenities_data.map do |amenity_data|
  Amenity.create!(
    account: account,
    name: amenity_data[:name],
    category: amenity_data[:category],
    icon: amenity_data[:icon],
    description: amenity_data[:description],
    is_countable: amenity_data[:is_countable] || false
  )
end

puts "Assigning amenities to hotel..."

# Add some amenities to the hotel
selected_hotel_amenities = ["Free WiFi", "Swimming Pool", "Fitness Center", "Restaurant", "Bar", "Free Parking", "Business Center", "Pet Friendly", "Wheelchair Accessible"]
selected_hotel_amenities.each do |amenity_name|
  amenity = Amenity.find_by(account: account, name: amenity_name)
  next unless amenity
  
  hotel_amenity = HotelAmenity.create!(
    account: account,
    hotel: hotel,
    amenity: amenity,
    is_available: true,
    is_free: !["Pet Friendly", "Spa"].include?(amenity_name)
  )
  
  # Add additional details for specific amenities
  case amenity_name
  when "Swimming Pool"
    hotel_amenity.update(location: "Rooftop", operating_hours: "6:00 AM - 10:00 PM")
  when "Fitness Center"
    hotel_amenity.update(location: "2nd Floor", operating_hours: "24/7")
  when "Restaurant"
    hotel_amenity.update(location: "Lobby Level", operating_hours: "6:30 AM - 11:00 PM")
  when "Bar"
    hotel_amenity.update(location: "Lobby", operating_hours: "4:00 PM - 1:00 AM")
  when "Pet Friendly"
    hotel_amenity.update(additional_cost: 50.00, notes: "Maximum 2 pets, under 50 lbs")
  end
end

puts "Creating room types..."

standard = RoomType.create!(
  account: account,
  hotel: hotel,
  name: "Standard Room",
  code: "STD",
  description: "Comfortable room with city view",
  base_price: 150.00,
  max_occupancy: 2,
  standard_occupancy: 2,
  extra_person_charge: 25.00,
  size_sqm: 25
)

deluxe = RoomType.create!(
  account: account,
  hotel: hotel,
  name: "Deluxe Room",
  code: "DLX",
  description: "Spacious room with premium amenities",
  base_price: 250.00,
  max_occupancy: 4,
  standard_occupancy: 2,
  extra_person_charge: 50.00,
  size_sqm: 35
)

puts "Assigning amenities to room types..."

# Standard room amenities
standard_room_amenities = [
  { name: "Air Conditioning", quantity: 1 },
  { name: "Flat Screen TV", quantity: 1 },
  { name: "Safe", quantity: 1 },
  { name: "Coffee Maker", quantity: 1 },
  { name: "Hair Dryer", quantity: 1 },
  { name: "Queen Bed", quantity: 1 },
  { name: "Work Desk", quantity: 1 }
]

standard_room_amenities.each do |amenity_info|
  amenity = Amenity.find_by(account: account, name: amenity_info[:name])
  next unless amenity
  
  RoomTypeAmenity.create!(
    account: account,
    room_type: standard,
    amenity: amenity,
    quantity: amenity_info[:quantity]
  )
end

# Deluxe room amenities
deluxe_room_amenities = [
  { name: "Air Conditioning", quantity: 1 },
  { name: "Flat Screen TV", quantity: 2 },
  { name: "Mini Bar", quantity: 1 },
  { name: "Safe", quantity: 1 },
  { name: "Coffee Maker", quantity: 1 },
  { name: "Hair Dryer", quantity: 1 },
  { name: "Iron & Ironing Board", quantity: 1 },
  { name: "Bathrobe", quantity: 2 },
  { name: "King Bed", quantity: 1 },
  { name: "Work Desk", quantity: 1 },
  { name: "Balcony", quantity: 1 },
  { name: "Sofa Bed", quantity: 1 }
]

deluxe_room_amenities.each do |amenity_info|
  amenity = Amenity.find_by(account: account, name: amenity_info[:name])
  next unless amenity
  
  RoomTypeAmenity.create!(
    account: account,
    room_type: deluxe,
    amenity: amenity,
    quantity: amenity_info[:quantity]
  )
end

puts "Creating hotel policies..."

# Create hotel policies
HotelPolicy.create!([
  {
    account: account,
    hotel: hotel,
    policy_type: "check_in",
    title: "Check-in Policy",
    description: "Check-in time is from 3:00 PM. Early check-in may be available upon request, subject to availability. Valid photo ID and credit card required at check-in.",
    sort_order: 1,
    is_active: true
  },
  {
    account: account,
    hotel: hotel,
    policy_type: "check_out",
    title: "Check-out Policy",
    description: "Check-out time is 11:00 AM. Late check-out may be available for an additional fee of $50 per hour, subject to availability.",
    sort_order: 2,
    is_active: true
  },
  {
    account: account,
    hotel: hotel,
    policy_type: "cancellation",
    title: "Standard Cancellation Policy",
    description: "Cancellations made 48 hours or more before check-in are fully refundable. Cancellations made within 48 hours are subject to a one-night charge.",
    sort_order: 3,
    is_active: true
  },
  {
    account: account,
    hotel: hotel,
    policy_type: "pet",
    title: "Pet Policy",
    description: "We welcome pets! A non-refundable fee of $50 per night applies. Maximum 2 pets per room, weight limit 50 lbs each. Pets must be leashed in public areas.",
    sort_order: 4,
    is_active: true
  },
  {
    account: account,
    hotel: hotel,
    policy_type: "smoking",
    title: "Non-Smoking Policy",
    description: "This is a 100% smoke-free property. Smoking is prohibited in all guest rooms and indoor areas. A $250 cleaning fee will be charged for violations.",
    sort_order: 5,
    is_active: true
  },
  {
    account: account,
    hotel: hotel,
    policy_type: "children",
    title: "Children & Extra Beds",
    description: "Children of all ages are welcome. Children under 12 stay free when using existing bedding. Cribs and rollaway beds available upon request for $25 per night.",
    sort_order: 6,
    is_active: true
  }
]) if defined?(HotelPolicy)

puts "Creating cancellation policies..."

# Create cancellation policies
if defined?(CancellationPolicy)
  # Account-wide default policies
  flexible_policy = CancellationPolicy.create!(
    account: account,
    hotel: nil,
    name: "Flexible Rate",
    code: "FLEX",
    description: "Free cancellation up to 24 hours before check-in. Full charge for late cancellations or no-shows.",
    cancellation_type: "flexible",
    free_cancellation_hours: 24,
    penalty_nights: 1,
    no_show_penalty: 100,
    is_refundable: true,
    is_default: true
  )

  moderate_policy = CancellationPolicy.create!(
    account: account,
    hotel: nil,
    name: "Moderate Rate",
    code: "MOD",
    description: "Free cancellation up to 5 days before check-in. One night charge for cancellations within 5 days.",
    cancellation_type: "moderate",
    free_cancellation_hours: 120,
    penalty_nights: 1,
    no_show_penalty: 100,
    is_refundable: true,
    is_default: false
  )

  strict_policy = CancellationPolicy.create!(
    account: account,
    hotel: nil,
    name: "Advance Purchase",
    code: "ADV",
    description: "50% refund for cancellations made 7+ days before check-in. No refund within 7 days of check-in.",
    cancellation_type: "strict",
    free_cancellation_hours: 168,
    penalty_percentage: 50,
    no_show_penalty: 100,
    is_refundable: true,
    is_default: false
  )

  non_refundable_policy = CancellationPolicy.create!(
    account: account,
    hotel: nil,
    name: "Non-Refundable Special",
    code: "NONREF",
    description: "This special rate is non-refundable and cannot be cancelled or modified once booked.",
    cancellation_type: "non_refundable",
    free_cancellation_hours: nil,
    penalty_percentage: 100,
    no_show_penalty: 100,
    is_refundable: false,
    is_default: false
  )

  # Hotel-specific policy
  hotel_special_policy = CancellationPolicy.create!(
    account: account,
    hotel: hotel,
    name: "SF Hotel Weekend Special",
    code: "SFWEEKEND",
    description: "Special weekend rate with free cancellation up to 72 hours before arrival.",
    cancellation_type: "flexible",
    free_cancellation_hours: 72,
    penalty_nights: 2,
    no_show_penalty: 100,
    is_refundable: true,
    is_default: false
  )
end

puts "Creating rooms..."

# Create standard rooms
(101..110).each do |num|
  Room.create!(
    account: account,
    hotel: hotel,
    room_type: standard,
    room_number: num.to_s,
    floor: "1",
    status: "available"
  )
end

# Create deluxe rooms
(201..210).each do |num|
  Room.create!(
    account: account,
    hotel: hotel,
    room_type: deluxe,
    room_number: num.to_s,
    floor: "2",
    status: "available"
  )
end

puts "Creating sample guest..."

guest = Guest.create!(
  account: account,
  first_name: "John",
  last_name: "Smith",
  email: "john.smith@example.com",
  phone: "555-0123",
  date_of_birth: Date.new(1985, 6, 15),
  address: "789 Oak Street",
  city: "San Francisco",
  state: "CA",
  country: "USA",
  postal_code: "94110"
)

puts "Creating sample reservation..."

room = hotel.rooms.available.first
Reservation.create!(
  account: account,
  hotel: hotel,
  guest: guest,
  room: room,
  room_type: room.room_type,
  check_in_date: 2.days.from_now.to_date,
  check_out_date: 4.days.from_now.to_date,
  status: "confirmed",
  adults: 2,
  children: 0,
  infants: 0,
  total_amount: 300.00,
  paid_amount: 300.00,
  payment_status: "paid",
  source: "website"
)

puts "Creating sample users with roles..."

# Use environment variable for all sandbox user passwords (required in production)
sandbox_password = ENV.fetch("SANDBOX_USER_PASSWORD") { ENV.fetch("PLATFORM_ADMIN_PASSWORD", "password123") }

# Create owner user
owner_user = User.create!(
  email: "owner@demohotels.com",
  first_name: "Alice",
  last_name: "Owner",
  password: sandbox_password,
  password_confirmation: sandbox_password
)

owner_account_user = AccountUser.create!(
  account: account,
  user: owner_user
)
owner_account_user.add_role :owner

# Create admin user
admin_user = User.create!(
  email: "admin@demohotels.com",
  first_name: "Bob",
  last_name: "Admin",
  password: sandbox_password,
  password_confirmation: sandbox_password
)

admin_account_user = AccountUser.create!(
  account: account,
  user: admin_user
)
admin_account_user.add_role :admin

# Create hotel manager user
manager_user = User.create!(
  email: "manager@demohotels.com",
  first_name: "Carol",
  last_name: "Manager",
  password: sandbox_password,
  password_confirmation: sandbox_password
)

manager_account_user = AccountUser.create!(
  account: account,
  user: manager_user
)
manager_account_user.add_role :hotel_manager

# Create front desk user
front_desk_user = User.create!(
  email: "frontdesk@demohotels.com",
  first_name: "David",
  last_name: "Receptionist",
  password: sandbox_password,
  password_confirmation: sandbox_password
)

front_desk_account_user = AccountUser.create!(
  account: account,
  user: front_desk_user
)
front_desk_account_user.add_role :front_desk

# Create housekeeping user
housekeeping_user = User.create!(
  email: "housekeeping@demohotels.com",
  first_name: "Emma",
  last_name: "Housekeeper",
  password: sandbox_password,
  password_confirmation: sandbox_password
)

housekeeping_account_user = AccountUser.create!(
  account: account,
  user: housekeeping_user
)
housekeeping_account_user.add_role :housekeeping

# Create maintenance staff user
maintenance_user = User.create!(
  email: "maintenance@demohotels.com",
  first_name: "Frank",
  last_name: "Technician",
  password: sandbox_password,
  password_confirmation: sandbox_password
)

maintenance_account_user = AccountUser.create!(
  account: account,
  user: maintenance_user
)
maintenance_account_user.add_role :hotel_manager  # Give them manager role for maintenance tasks

puts "Creating housekeeping tasks..." if defined?(HousekeepingTask)

if defined?(HousekeepingTask)
  # Create housekeeping tasks for various rooms
  hotel.rooms.limit(10).each_with_index do |room, index|
    # Create a mix of task types and statuses
    case index % 4
    when 0
      HousekeepingTask.create!(
        account: account,
        hotel: hotel,
        room: room,
        assigned_to: housekeeping_user,
        task_type: "cleaning",
        priority: "normal",
        status: "pending",
        scheduled_date: Date.current,
        scheduled_time: Time.parse("09:00"),
        notes: "Regular daily cleaning"
      )
    when 1
      HousekeepingTask.create!(
        account: account,
        hotel: hotel,
        room: room,
        assigned_to: housekeeping_user,
        task_type: "inspection",
        priority: "high",
        status: "in_progress",
        scheduled_date: Date.current,
        scheduled_time: Time.parse("14:00"),
        started_at: 1.hour.ago,
        notes: "Quality inspection after guest checkout"
      )
    when 2
      HousekeepingTask.create!(
        account: account,
        hotel: hotel,
        room: room,
        assigned_to: housekeeping_user,
        task_type: "turndown",
        priority: "normal",
        status: "completed",
        scheduled_date: Date.yesterday,
        scheduled_time: Time.parse("18:00"),
        started_at: Date.yesterday.to_time + 18.hours,
        completed_at: Date.yesterday.to_time + 18.hours + 30.minutes,
        inspection_score: 9,
        notes: "Evening turndown service completed"
      )
    when 3
      HousekeepingTask.create!(
        account: account,
        hotel: hotel,
        room: room,
        assigned_to: nil,  # Unassigned task
        task_type: "deep_clean",
        priority: "urgent",
        status: "pending",
        scheduled_date: Date.tomorrow,
        scheduled_time: Time.parse("10:00"),
        notes: "Deep cleaning required before VIP guest arrival"
      )
    end
  end
end

puts "Creating maintenance requests..." if defined?(MaintenanceRequest)

if defined?(MaintenanceRequest)
  # Create various maintenance requests
  maintenance_issues = [
    { category: "electrical", issue: "Flickering lights in bathroom", priority: "normal", room: hotel.rooms.first },
    { category: "plumbing", issue: "Slow drain in shower", priority: "low", room: hotel.rooms.second },
    { category: "hvac", issue: "AC unit making loud noise", priority: "high", room: hotel.rooms.third },
    { category: "furniture", issue: "Broken desk chair wheel", priority: "low", room: hotel.rooms.fourth },
    { category: "electrical", issue: "Power outlet not working", priority: "critical", room: hotel.rooms.fifth },
    { category: "plumbing", issue: "Toilet running constantly", priority: "normal", room: hotel.rooms[5] },
    { category: "other", issue: "Window blind mechanism stuck", priority: "low", room: hotel.rooms[6] },
    { category: "electrical", issue: "Lobby chandelier lights flickering", priority: "high", room: nil },  # Common area
    { category: "hvac", issue: "Conference room too cold", priority: "normal", room: nil },  # Common area
    { category: "other", issue: "Elevator display screen blank", priority: "critical", room: nil }  # Common area
  ]
  
  maintenance_issues.each_with_index do |issue_data, index|
    status = case index % 5
             when 0 then "open"
             when 1 then "in_progress"
             when 2 then "pending_parts"
             when 3 then "completed"
             else "open"
             end
    
    request = MaintenanceRequest.create!(
      account: account,
      hotel: hotel,
      room: issue_data[:room],
      reported_by: front_desk_user,
      assigned_to: status == "open" ? nil : maintenance_user,
      category: issue_data[:category],
      issue_description: issue_data[:issue],
      priority: issue_data[:priority],
      status: status,
      cost_estimate: rand(50..500),
      scheduled_date: status == "completed" ? Date.yesterday : Date.tomorrow
    )
    
    # Add completion details for completed requests
    if status == "completed"
      request.update!(
        completed_at: Date.yesterday.to_time + rand(8..16).hours,
        actual_cost: request.cost_estimate * (0.8 + rand * 0.4),  # Actual cost within 80-120% of estimate
        resolution_notes: "Issue resolved successfully. Replaced faulty components."
      )
    end
  end
end

# Create Services
puts "Creating services..."

# Clear existing service data
ServiceBooking.destroy_all
ServiceSchedule.destroy_all
Service.destroy_all

[Hotel.first, Hotel.second].compact.each do |hotel|
  next unless hotel  # Skip if hotel doesn't exist
  
  # Spa service
  spa_service = Service.create!(
    account: account,
    hotel: hotel,
    name: "#{hotel.name} Spa Treatment",
    category: "spa",
    description: "Full body massage, facial, and aromatherapy session",
    price: 250.00,
    pricing_unit: "fixed",
    duration_minutes: 120,
    capacity: 4,
    advance_booking_required: true,
    is_active: true
  )

  # Restaurant service
  restaurant_service = Service.create!(
    account: account,
    hotel: hotel,
    name: "#{hotel.name} Restaurant",
    category: "dining",
    description: "Award-winning cuisine with seasonal menu",
    price: 85.00,
    pricing_unit: "per_person",
    duration_minutes: 90,
    capacity: 50,
    advance_booking_required: false,
    is_active: true
  )

  # Airport shuttle
  airport_shuttle = Service.create!(
    account: account,
    hotel: hotel,
    name: "Airport Shuttle Service",
    category: "transport",
    description: "Comfortable shuttle service to and from the airport",
    price: 45.00,
    pricing_unit: "per_person",
    duration_minutes: 45,
    capacity: 8,
    advance_booking_required: true,
    is_active: true
  )

  # Laundry service
  laundry_service = Service.create!(
    account: account,
    hotel: hotel,
    name: "Premium Laundry Service",
    category: "laundry",
    description: "Same-day laundry and dry cleaning service",
    price: 5.00,
    pricing_unit: "per_item",
    duration_minutes: nil,
    capacity: nil,
    advance_booking_required: false,
    is_active: true
  )

  # Create Service Schedules for spa (Monday-Saturday, 9 AM - 8 PM)
  (1..6).each do |day|
    ServiceSchedule.create!(
      account: account,
      service: spa_service,
      day_of_week: day,
      start_time: "09:00",
      end_time: "20:00",
      max_bookings_per_slot: 4
    )
  end

  # Restaurant schedule (Every day, all day service)
  7.times do |day|
    ServiceSchedule.create!(
      account: account,
      service: restaurant_service,
      day_of_week: day,
      start_time: "11:30",
      end_time: "22:00",
      max_bookings_per_slot: 50
    )
  end

  # Create some service bookings for current guests
  current_reservation = Reservation.where(hotel: hotel, status: "checked_in").first
  
  if current_reservation
    # Spa booking for tomorrow
    ServiceBooking.create!(
      account: account,
      service: spa_service,
      guest: current_reservation.guest,
      reservation: current_reservation,
      booking_date: Date.tomorrow,
      booking_time: "14:00",
      duration_minutes: 120,
      quantity: 1,
      unit_price: 250.00,
      total_price: 250.00,
      status: "confirmed",
      notes: "Requested aromatherapy with lavender"
    )
    
    # Restaurant booking for tonight
    ServiceBooking.create!(
      account: account,
      service: restaurant_service,
      guest: current_reservation.guest,
      reservation: current_reservation,
      booking_date: Date.current,
      booking_time: "19:00",
      duration_minutes: 90,
      quantity: 2,
      unit_price: 85.00,
      total_price: 170.00,
      status: "confirmed",
      notes: "Window table requested"
    )
  end

  # Future bookings
  Reservation.where(hotel: hotel, status: ["pending", "confirmed"]).limit(3).each do |reservation|
    # Airport shuttle
    ServiceBooking.create!(
      account: account,
      service: airport_shuttle,
      guest: reservation.guest,
      reservation: reservation,
      booking_date: reservation.check_in_date,
      booking_time: "10:00",
      duration_minutes: 45,
      quantity: reservation.adults,
      unit_price: 45.00,
      total_price: 45.00 * reservation.adults,
      status: "confirmed",
      notes: "Flight arrives at 9:15 AM"
    )
  end

  # Past completed bookings
  Reservation.where(hotel: hotel, status: "checked_out").limit(2).each do |reservation|
    ServiceBooking.create!(
      account: account,
      service: laundry_service,
      guest: reservation.guest,
      reservation: reservation,
      booking_date: reservation.check_in_date + 1.day,
      booking_time: nil,
      duration_minutes: nil,
      quantity: 8,
      unit_price: 5.00,
      total_price: 40.00,
      status: "completed",
      notes: "Express service requested"
    )
  end
end

puts "Enabling AI deployments for hotel..."

# Enable AI deployments for the hotel
HotelAiSetting.create!(
  account: account,
  hotel: hotel,
  deployment_key: 'web_chat',
  enabled: true,
  settings: {}
)

# =============================================================================
# PLATFORM ADMIN USER
# =============================================================================
# This user has access to the /admin area for managing student accounts

puts "\nCreating platform admin user..."

platform_admin_email = ENV.fetch("PLATFORM_ADMIN_EMAIL", "admin@platform.local")

platform_admin = User.find_or_create_by!(email: platform_admin_email) do |u|
  u.first_name = "Platform"
  u.last_name = "Admin"
  u.password = sandbox_password
  u.is_platform_admin = true
end
platform_admin.update!(is_platform_admin: true) unless platform_admin.is_platform_admin?

# Also make the owner user a platform admin for convenience
owner_user.update!(is_platform_admin: true)

# Create the shared AI agent user (used when creating student accounts via admin UI)
User.find_or_create_by!(email: "ai-agent@system.local") do |u|
  u.first_name = "AI"
  u.last_name = "Agent"
  u.password = SecureRandom.hex(32)
  u.is_ai_agent = true
end

puts "\n=== Seeding complete! ==="
puts "Created:"
puts "- #{Account.count} Account(s)"
puts "- #{Hotel.count} Hotel(s)"
puts "- #{RoomType.count} Room Type(s)"
puts "- #{Room.count} Room(s)"
puts "- #{Guest.count} Guest(s)"
puts "- #{Reservation.count} Reservation(s)"
puts "- #{HotelPolicy.count} Hotel Policy(ies)" if defined?(HotelPolicy)
puts "- #{CancellationPolicy.count} Cancellation Policy(ies)" if defined?(CancellationPolicy)
puts "- #{HousekeepingTask.count} Housekeeping Task(s)" if defined?(HousekeepingTask)
puts "- #{MaintenanceRequest.count} Maintenance Request(s)" if defined?(MaintenanceRequest)
puts "- #{Service.count} Service(s)"
puts "- #{ServiceSchedule.count} Service Schedule(s)"
puts "- #{ServiceBooking.count} Service Booking(s)"

puts "\n=== Platform Admin ==="
puts "Email: #{platform_admin_email}"
puts "Password: #{sandbox_password == 'password123' ? 'password123 (default - change in production!)' : '[set via env var]'}"
puts "Admin URL: /admin"
puts "\nAll sandbox users share the same password (set via SANDBOX_USER_PASSWORD or PLATFORM_ADMIN_PASSWORD env var)."
