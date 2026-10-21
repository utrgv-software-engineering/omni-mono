require "test_helper"

class Api::V1::AvailabilityTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    
    @single_room_type = create(:room_type, 
      hotel: @hotel,
      account: @account,
      name: "Single Room",
      base_price: 100,
      standard_occupancy: 1,
      max_occupancy: 2,
      extra_person_charge: 0
    )
    
    @double_room_type = create(:room_type,
      hotel: @hotel,
      account: @account,
      name: "Double Room", 
      base_price: 150,
      standard_occupancy: 2,
      max_occupancy: 4,
      extra_person_charge: 25
    )
    
    # Create available rooms
    create_list(:room, 3, hotel: @hotel, room_type: @single_room_type, account: @account, status: 'available')
    create_list(:room, 2, hotel: @hotel, room_type: @double_room_type, account: @account, status: 'available')
    
    @api_agent = create_api_agent(@account, :front_desk)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/availability returns available room types" do
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: Date.tomorrow.to_s,
          check_out_date: (Date.tomorrow + 2.days).to_s,
          adults: 2
        },
        headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data.length
    
    # Check first room type (should be single room, sorted by price)
    first_room = data.first
    assert_equal @single_room_type.id, first_room['room_type']['id']
    assert_equal 3, first_room['available_count']
    assert_equal '200.0', first_room['total_price'] # 2 nights * $100
    assert_equal 2, first_room['nights']
    
    # Check second room type
    second_room = data.second
    assert_equal @double_room_type.id, second_room['room_type']['id']
    assert_equal 2, second_room['available_count']
    assert_equal '300.0', second_room['total_price'] # 2 nights * $150
  end
  
  test "GET /api/v1/hotels/:hotel_id/availability filters by max occupancy" do
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: Date.tomorrow.to_s,
          check_out_date: (Date.tomorrow + 1.day).to_s,
          adults: 3,
          children: 1
        },
        headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    data = json_response['data']
    
    # Only double room can accommodate 4 guests
    assert_equal 1, data.length
    assert_equal @double_room_type.id, data.first['room_type']['id']
  end
  
  test "GET /api/v1/hotels/:hotel_id/availability calculates extra person charges" do
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: Date.tomorrow.to_s,
          check_out_date: (Date.tomorrow + 1.day).to_s,
          adults: 3  # 1 extra person for double room
        },
        headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    data = json_response['data']
    
    double_room = data.find { |r| r['room_type']['id'] == @double_room_type.id }
    # Base price $150 + extra person charge $25 = $175 for 1 night
    assert_equal '175.0', double_room['total_price']
  end
  
  test "GET /api/v1/hotels/:hotel_id/availability excludes unavailable rooms" do
    # Make all single rooms unavailable
    @single_room_type.rooms.update_all(status: 'occupied')
    
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: Date.tomorrow.to_s,
          check_out_date: (Date.tomorrow + 1.day).to_s,
          adults: 1
        },
        headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    data = json_response['data']
    
    # Only double rooms should be available
    assert_equal 1, data.length
    assert_equal @double_room_type.id, data.first['room_type']['id']
  end
  
  test "GET /api/v1/hotels/:hotel_id/availability validates required parameters" do
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: { adults: 1 },
        headers: @headers
    
    assert_api_conform(status: 400)

    
    assert_response :bad_request
    assert_json_response
  end
  
  test "GET /api/v1/hotels/:hotel_id/availability validates date format" do
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: "invalid-date",
          check_out_date: Date.tomorrow.to_s,
          adults: 1
        },
        headers: @headers
    
    assert_api_conform(status: 400)

    
    assert_response :bad_request
    assert_json_response
    assert_match /Invalid date format/, json_response['message']
  end
  
  test "GET /api/v1/hotels/:hotel_id/availability validates date order" do
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: (Date.tomorrow + 2.days).to_s,
          check_out_date: Date.tomorrow.to_s,
          adults: 1
        },
        headers: @headers
    
    assert_api_conform(status: 400)

    
    assert_response :bad_request
    assert_json_response
    assert_match /Check-out date must be after check-in date/, json_response['message']
  end
  
  test "housekeeping role can check availability" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)
    
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: Date.tomorrow.to_s,
          check_out_date: (Date.tomorrow + 1.day).to_s,
          adults: 1
        },
        headers: headers
    
    assert_api_conform(status: 200)


    assert_response :success
  end

  test "returns 401 without API key" do
    get "/api/v1/hotels/#{@hotel.id}/availability",
        params: {
          check_in_date: Date.tomorrow.to_s,
          check_out_date: (Date.tomorrow + 1.day).to_s,
          adults: 1
        }

    assert_response :unauthorized
  end

  test "returns 404 for hotel from different account" do
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)

    get "/api/v1/hotels/#{other_hotel.id}/availability",
        params: {
          check_in_date: Date.tomorrow.to_s,
          check_out_date: (Date.tomorrow + 1.day).to_s,
          adults: 1
        },
        headers: @headers

    assert_response :not_found
  end
end