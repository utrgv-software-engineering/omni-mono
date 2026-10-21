# frozen_string_literal: true

# Evaluation API controller for agent testing.
#
# This controller provides endpoints for:
# - Loading database fixtures before scenarios
# - Verifying expected state after scenarios
# - Cleaning up after scenarios
#
# SECURITY: This controller is ONLY available in test environment
# or when ALLOW_EVAL_API=true is set. It bypasses normal authentication.
#
class Api::V1::EvalController < ActionController::API
  before_action :ensure_eval_environment!
  before_action :validate_eval_token!

  # POST /api/v1/eval/setup
  # Load a fixture and prepare the evaluation environment.
  #
  # Request body:
  #   {
  #     "fixture": "base_hotel_with_availability",
  #     "config": { ... optional overrides ... }
  #   }
  #
  # Response:
  #   {
  #     "success": true,
  #     "session_id": "abc123",
  #     "hotel_id": 1,
  #     "account_id": 1,
  #     "api_key": "eval_key_abc123"
  #   }
  #
  def setup
    fixture_name = params[:fixture] || 'base_hotel'
    config = params[:config] || {}

    # Generate a unique session ID for this evaluation run
    session_id = SecureRandom.uuid

    # NOTE: We no longer clean the database on setup to support parallel execution.
    # Each scenario creates isolated data with unique IDs.
    # Cleanup happens in teardown, scoped to the session.

    # Load the fixture
    fixture_data = load_fixture(fixture_name, config)

    # Create a temporary API key for this eval session
    api_key = create_eval_api_key(fixture_data[:account], session_id)

    render json: {
      success: true,
      session_id: session_id,
      hotel_id: fixture_data[:hotel]&.id,
      account_id: fixture_data[:account]&.id,
      api_key: api_key,
      fixture_loaded: fixture_name,
      data: fixture_data[:summary]
    }
  rescue StandardError => e
    render json: {
      success: false,
      error: e.message,
      backtrace: Rails.env.development? ? e.backtrace&.first(5) : nil
    }, status: :unprocessable_entity
  end

  # POST /api/v1/eval/verify
  # Verify expected state against actual database state.
  #
  # Request body:
  #   {
  #     "session_id": "abc123",
  #     "expected": {
  #       "reservation": {
  #         "exists": true,
  #         "match": {
  #           "guest_email": "john@test.com",
  #           "status": ["pending", "confirmed"]
  #         }
  #       },
  #       "guest": {
  #         "exists": true,
  #         "count": 1,
  #         "match": { "email": "john@test.com" }
  #       }
  #     }
  #   }
  #
  # Response:
  #   {
  #     "success": true,
  #     "results": {
  #       "reservation": { "passed": true, ... },
  #       "guest": { "passed": true, ... }
  #     }
  #   }
  #
  def verify
    session_id = params[:session_id]
    expected = params[:expected] || {}
    results = {}

    # Find the account for this session and set tenant
    account = find_session_account(session_id)
    unless account
      render json: { success: false, error: 'Session not found' }, status: :not_found
      return
    end
    ActsAsTenant.current_tenant = account

    # Verify each expected entity type (automatically scoped by acts_as_tenant)
    results[:reservation] = verify_reservation(expected[:reservation]) if expected[:reservation]
    results[:guest] = verify_guest(expected[:guest]) if expected[:guest]
    results[:room] = verify_room(expected[:room]) if expected[:room]

    # Check if all verifications passed
    all_passed = results.values.all? { |r| r[:passed] }

    render json: {
      success: all_passed,
      results: results
    }
  end

  # POST /api/v1/eval/query
  # Query database state for custom verification in measures.
  #
  # Request body:
  #   {
  #     "session_id": "abc123",
  #     "queries": {
  #       "reservations": { "status": "confirmed" },
  #       "guests": { "email": "john@test.com" }
  #     }
  #   }
  #
  # Response:
  #   {
  #     "reservations": { "count": 1, "data": [...] },
  #     "guests": { "count": 1, "data": [...] }
  #   }
  #
  def query
    queries = params[:queries] || {}
    results = {}

    results[:reservations] = query_reservations(queries[:reservations]) if queries[:reservations]
    results[:guests] = query_guests(queries[:guests]) if queries[:guests]
    results[:rooms] = query_rooms(queries[:rooms]) if queries[:rooms]

    render json: results
  end

  # DELETE /api/v1/eval/teardown
  # Clean up after an evaluation scenario.
  #
  # Request body:
  #   { "session_id": "abc123" }
  #
  def teardown
    session_id = params[:session_id]

    if session_id
      # Find the account_user for this session to get the account
      account_user = AccountUser.find_by("api_key LIKE ?", "eval_#{session_id}%")

      if account_user
        account = account_user.account

        # Clean up only this session's data (scoped to account)
        ActiveRecord::Base.transaction do
          # Delete reservations for this account's hotels
          Reservation.joins(:hotel).where(hotels: { account_id: account.id }).destroy_all
          Guest.where(account_id: account.id).destroy_all
          Room.joins(:hotel).where(hotels: { account_id: account.id }).destroy_all
          RoomTypeAmenity.joins(:room_type).where(room_types: { account_id: account.id }).destroy_all
          RoomType.where(account_id: account.id).destroy_all
          HotelAmenity.joins(:hotel).where(hotels: { account_id: account.id }).destroy_all
          HotelPolicy.where(account_id: account.id).destroy_all if defined?(HotelPolicy)
          HotelAiSetting.where(account_id: account.id).destroy_all if defined?(HotelAiSetting)
          Hotel.where(account_id: account.id).destroy_all
          account_user.destroy
          account.destroy
        end
      end
    end

    render json: { success: true }
  end

  private

  def ensure_eval_environment!
    unless Rails.env.test? || ENV['ALLOW_EVAL_API'] == 'true'
      render json: {
        error: 'Forbidden',
        message: 'Eval API is only available in test environment or with ALLOW_EVAL_API=true'
      }, status: :forbidden
    end
  end

  def validate_eval_token!
    # Require a special eval token for additional security
    eval_token = request.headers['X-Eval-Token'] || params[:eval_token]
    expected_token = ENV.fetch('EVAL_API_TOKEN', 'eval-development-token')

    unless ActiveSupport::SecurityUtils.secure_compare(eval_token.to_s, expected_token)
      render json: {
        error: 'Unauthorized',
        message: 'Invalid or missing eval token'
      }, status: :unauthorized
    end
  end

  def parse_date(value, default = nil)
    return default if value.nil?
    return value if value.is_a?(Date)
    Date.parse(value.to_s)
  rescue ArgumentError
    default
  end

  def find_session_account(session_id)
    return nil unless session_id
    account_user = AccountUser.find_by("api_key LIKE ?", "eval_#{session_id}%")
    account_user&.account
  end

  def clean_database!
    # Clean in reverse dependency order
    ActiveRecord::Base.transaction do
      # Transactional data
      ServiceBooking.destroy_all if defined?(ServiceBooking)
      HousekeepingTask.destroy_all if defined?(HousekeepingTask)
      MaintenanceRequest.destroy_all if defined?(MaintenanceRequest)
      Reservation.destroy_all
      Guest.destroy_all

      # Room data
      Room.destroy_all
      RoomTypeAmenity.destroy_all
      RoomType.destroy_all

      # Hotel data
      HotelAmenity.destroy_all
      ServiceSchedule.destroy_all if defined?(ServiceSchedule)
      Service.destroy_all if defined?(Service)
      HotelPolicy.destroy_all if defined?(HotelPolicy)
      CancellationPolicy.destroy_all if defined?(CancellationPolicy)
      HotelAiSetting.destroy_all if defined?(HotelAiSetting)
      Hotel.destroy_all

      # Account data (except system users)
      AccountUser.where.not(user: User.where(is_ai_agent: true)).destroy_all
      Amenity.destroy_all
      Account.destroy_all

      # Clean up non-AI users
      User.where(is_ai_agent: false).destroy_all
    end
  end

  def load_fixture(fixture_name, config = {})
    # Fixture definitions - these mirror db/seeds.rb but are more targeted
    case fixture_name.to_s
    when 'base_hotel', 'base_hotel_with_availability'
      load_base_hotel_fixture(config)
    when 'hotel_with_returning_guest'
      data = load_base_hotel_fixture(config)
      add_returning_guest(data, config)
      data
    when 'hotel_with_reservation', 'hotel_with_existing_reservation'
      data = load_base_hotel_fixture(config)
      add_existing_reservation(data, config)
      data
    when 'hotel_fully_booked'
      data = load_base_hotel_fixture(config)
      book_all_rooms(data, config)
      data
    else
      raise ArgumentError, "Unknown fixture: #{fixture_name}"
    end
  end

  def load_base_hotel_fixture(config = {})
    account = Account.create!(
      name: config[:account_name] || 'Eval Hotel Group',
      subdomain: config[:subdomain] || "eval#{SecureRandom.hex(4)}",
      address: '123 Eval Street',
      city: 'San Francisco',
      state: 'CA',
      country: 'USA',
      postal_code: '94102',
      phone: '415-555-0100',
      email: 'eval@example.com',
      timezone: 'Pacific Time (US & Canada)',
      currency: 'USD',
      status: 'active'
    )

    hotel = Hotel.create!(
      account: account,
      name: config[:hotel_name] || 'Eval Hotel',
      subdomain: 'eval',
      address: '456 Test Street',
      city: 'San Francisco',
      state: 'CA',
      country: 'USA',
      postal_code: '94103',
      phone: '415-555-0200',
      email: 'hotel@eval.example.com',
      timezone: 'Pacific Time (US & Canada)',
      star_rating: 4.0,
      total_rooms: config[:total_rooms] || 10
    )

    # Create room types
    standard = RoomType.create!(
      account: account,
      hotel: hotel,
      name: 'Standard Room',
      code: 'STD',
      description: 'Comfortable standard room',
      base_price: config[:standard_price] || 150.00,
      max_occupancy: 2,
      standard_occupancy: 2,
      extra_person_charge: 25.00,
      size_sqm: 25
    )

    deluxe = RoomType.create!(
      account: account,
      hotel: hotel,
      name: 'Deluxe Room',
      code: 'DLX',
      description: 'Spacious deluxe room',
      base_price: config[:deluxe_price] || 250.00,
      max_occupancy: 4,
      standard_occupancy: 2,
      extra_person_charge: 50.00,
      size_sqm: 35
    )

    # Create rooms
    rooms = []
    5.times do |i|
      rooms << Room.create!(
        account: account,
        hotel: hotel,
        room_type: standard,
        room_number: (101 + i).to_s,
        floor: '1',
        status: 'available'
      )
    end

    5.times do |i|
      rooms << Room.create!(
        account: account,
        hotel: hotel,
        room_type: deluxe,
        room_number: (201 + i).to_s,
        floor: '2',
        status: 'available'
      )
    end

    # Enable AI for this hotel
    HotelAiSetting.create!(
      account: account,
      hotel: hotel,
      deployment_key: 'web_chat',
      enabled: true,
      settings: {}
    ) if defined?(HotelAiSetting)

    {
      account: account,
      hotel: hotel,
      room_types: { standard: standard, deluxe: deluxe },
      rooms: rooms,
      summary: {
        account_id: account.id,
        hotel_id: hotel.id,
        room_count: rooms.count,
        room_types: %w[STD DLX]
      }
    }
  end

  def add_returning_guest(data, config = {})
    guest = Guest.create!(
      account: data[:account],
      first_name: config[:guest_first_name] || 'John',
      last_name: config[:guest_last_name] || 'Returning',
      email: config[:guest_email] || 'returning@test.com',
      phone: config[:guest_phone] || '555-1234',
      address: '123 Guest St',
      city: 'San Francisco',
      state: 'CA',
      country: 'USA',
      postal_code: '94110'
    )

    data[:guest] = guest
    data[:summary][:guest_email] = guest.email
    data
  end

  def add_existing_reservation(data, config = {})
    # First add the guest
    add_returning_guest(data, config) unless data[:guest]

    check_in = parse_date(config[:check_in_date], 5.days.from_now.to_date)
    check_out = parse_date(config[:check_out_date], 7.days.from_now.to_date)
    confirmation = config[:confirmation_number] || "EVAL#{SecureRandom.hex(4).upcase}"

    room = data[:rooms].find { |r| r.status == 'available' }

    reservation = Reservation.create!(
      account: data[:account],
      hotel: data[:hotel],
      guest: data[:guest],
      room: room,
      room_type: room.room_type,
      check_in_date: check_in,
      check_out_date: check_out,
      confirmation_number: confirmation,
      status: config[:status] || 'confirmed',
      adults: config[:adults] || 2,
      children: config[:children] || 0,
      infants: 0,
      total_amount: room.room_type.base_price * (check_out - check_in).to_i,
      paid_amount: 0,
      payment_status: 'pending',
      source: 'website'
    )

    data[:reservation] = reservation
    data[:summary][:confirmation_number] = confirmation
    data[:summary][:reservation_id] = reservation.id
    data
  end

  def book_all_rooms(data, config = {})
    check_in = parse_date(config[:check_in_date], Date.current)
    check_out = parse_date(config[:check_out_date], 3.days.from_now.to_date)

    data[:rooms].each_with_index do |room, i|
      guest = Guest.create!(
        account: data[:account],
        first_name: "Guest",
        last_name: "#{i + 1}",
        email: "guest#{i + 1}@booked.com",
        phone: "555-000#{i}"
      )

      Reservation.create!(
        account: data[:account],
        hotel: data[:hotel],
        guest: guest,
        room: room,
        room_type: room.room_type,
        check_in_date: check_in,
        check_out_date: check_out,
        confirmation_number: "BOOK#{SecureRandom.hex(4).upcase}",
        status: 'confirmed',
        adults: 2,
        children: 0,
        infants: 0,
        total_amount: room.room_type.base_price * (check_out - check_in).to_i,
        paid_amount: 0,
        payment_status: 'pending',
        source: 'website'
      )

      room.update!(status: 'occupied')
    end

    data[:summary][:all_rooms_booked] = true
    data[:summary][:booked_dates] = { check_in: check_in, check_out: check_out }
    data
  end

  def create_eval_api_key(account, session_id)
    # Create a temporary AI agent user for eval
    eval_user = User.find_or_create_by!(email: "eval-agent@system.local") do |u|
      u.first_name = 'Eval'
      u.last_name = 'Agent'
      u.password = SecureRandom.hex(32)
      u.is_ai_agent = true
    end

    api_key = "eval_#{session_id}_#{SecureRandom.hex(16)}"

    account_user = AccountUser.create!(
      account: account,
      user: eval_user,
      api_key: api_key
    )
    account_user.add_role(:admin)

    api_key
  end

  # Verification helpers

  def verify_reservation(expected)
    query = Reservation.all

    if expected[:match]
      expected[:match].each do |key, value|
        query = apply_reservation_filter(query, key.to_s, value)
      end
    end

    found = query.exists?
    count = query.count
    record = found ? query.first : nil

    passed = case
             when expected[:exists] == true then found
             when expected[:exists] == false then !found
             when expected[:count] then count == expected[:count].to_i
             else found
             end

    {
      passed: passed,
      expected_exists: expected[:exists],
      actual_exists: found,
      expected_count: expected[:count],
      actual_count: count,
      found_record: record&.as_json(only: %i[id confirmation_number status check_in_date check_out_date])
    }
  end

  def apply_reservation_filter(query, key, value)
    case key
    when 'guest_email'
      query.joins(:guest).where(guests: { email: value })
    when 'room_type_code'
      query.joins(:room_type).where(room_types: { code: value })
    when 'status'
      query.where(status: Array(value))
    when 'confirmation_number'
      query.where(confirmation_number: value)
    else
      query.where(key => value)
    end
  end

  def verify_guest(expected)
    query = Guest.all

    if expected[:match]
      expected[:match].each do |key, value|
        query = query.where(key => value)
      end
    end

    found = query.exists?
    count = query.count

    passed = case
             when expected[:exists] == true then found
             when expected[:exists] == false then !found
             when expected[:count] then count == expected[:count].to_i
             else found
             end

    {
      passed: passed,
      expected_exists: expected[:exists],
      actual_exists: found,
      expected_count: expected[:count],
      actual_count: count
    }
  end

  def verify_room(expected)
    query = Room.all

    if expected[:match]
      expected[:match].each do |key, value|
        query = query.where(key => value)
      end
    end

    {
      passed: query.exists?,
      count: query.count
    }
  end

  def query_reservations(filters)
    query = Reservation.all
    filters.each do |key, value|
      query = apply_reservation_filter(query, key.to_s, value)
    end

    {
      count: query.count,
      exists: query.exists?,
      data: query.limit(10).as_json(
        only: %i[id confirmation_number status check_in_date check_out_date total_amount],
        include: { guest: { only: %i[id first_name last_name email] } }
      )
    }
  end

  def query_guests(filters)
    query = Guest.all
    filters.each do |key, value|
      query = query.where(key => value)
    end

    {
      count: query.count,
      exists: query.exists?,
      data: query.limit(10).as_json(only: %i[id first_name last_name email phone])
    }
  end

  def query_rooms(filters)
    query = Room.all
    filters.each do |key, value|
      query = query.where(key => value)
    end

    {
      count: query.count,
      exists: query.exists?,
      data: query.limit(10).as_json(
        only: %i[id room_number floor status],
        include: { room_type: { only: %i[id name code base_price] } }
      )
    }
  end
end
