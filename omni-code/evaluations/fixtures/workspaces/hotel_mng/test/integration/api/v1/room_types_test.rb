require "test_helper"

class Api::V1::RoomTypesTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)

    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end

  # Index tests
  test "GET /api/v1/hotels/:hotel_id/room_types returns list of room types" do
    # Create additional room types
    room_type2 = create(:room_type, hotel: @hotel, account: @account, name: "Deluxe Suite")
    room_type3 = create(:room_type, hotel: @hotel, account: @account, name: "Standard Room")

    get "/api/v1/hotels/#{@hotel.id}/room_types", headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_equal 3, data.size
  end

  test "GET /api/v1/hotels/:hotel_id/room_types returns room types ordered by name" do
    create(:room_type, hotel: @hotel, account: @account, name: "Zebra Suite")
    create(:room_type, hotel: @hotel, account: @account, name: "Alpha Room")

    get "/api/v1/hotels/#{@hotel.id}/room_types", headers: @headers

    assert_response :success

    data = json_response['data']
    names = data.map { |rt| rt['name'] }
    assert_equal names.sort, names
  end

  test "GET /api/v1/hotels/:hotel_id/room_types includes pagination metadata" do
    get "/api/v1/hotels/#{@hotel.id}/room_types", headers: @headers

    assert_response :success

    meta = json_response['meta']
    assert meta.key?('current_page')
    assert meta.key?('total_pages')
    assert meta.key?('total_count')
    assert meta.key?('per_page')
  end

  test "GET /api/v1/hotels/:hotel_id/room_types respects pagination parameters" do
    # Create more room types to test pagination
    5.times { |i| create(:room_type, hotel: @hotel, account: @account, name: "Room Type #{i}") }

    get "/api/v1/hotels/#{@hotel.id}/room_types",
        params: { page: 1, per_page: 2 },
        headers: @headers

    assert_response :success

    data = json_response['data']
    meta = json_response['meta']

    # Total is 6 (1 from setup + 5 created), but only 2 per page
    assert_equal 2, data.size
    assert_equal 6, meta['total_count']
    assert_equal 1, meta['current_page']
    assert_equal 2, meta['per_page']
  end

  # Show tests
  test "GET /api/v1/hotels/:hotel_id/room_types/:id returns room type details" do
    get "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}", headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_equal @room_type.id, data['id']
    assert_equal @room_type.name, data['name']
    assert_equal @room_type.code, data['code']
    assert_equal @room_type.base_price.to_s, data['base_price']
    assert_equal @room_type.max_occupancy, data['max_occupancy']
    assert_equal @room_type.standard_occupancy, data['standard_occupancy']
  end

  test "GET /api/v1/hotels/:hotel_id/room_types/:id returns 404 for non-existent room type" do
    get "/api/v1/hotels/#{@hotel.id}/room_types/999999", headers: @headers

    assert_response :not_found
    assert_json_response
    assert_equal 'Not Found', json_response['error']
  end

  # Create tests
  test "POST /api/v1/hotels/:hotel_id/room_types creates a new room type" do
    room_type_params = {
      name: "Presidential Suite",
      code: "PRES",
      description: "Luxurious presidential suite",
      base_price: 999.99,
      standard_occupancy: 2,
      max_occupancy: 4,
      extra_person_charge: 100.00,
      size_sqm: 120.5
    }

    assert_difference('RoomType.count', 1) do
      post "/api/v1/hotels/#{@hotel.id}/room_types",
           params: room_type_params.to_json,
           headers: @headers
    end

    assert_response :created
    assert_json_response

    data = json_response['data']
    assert_equal "Presidential Suite", data['name']
    assert_equal "PRES", data['code']
    assert_equal "999.99", data['base_price']
    assert_equal 2, data['standard_occupancy']
    assert_equal 4, data['max_occupancy']
  end

  test "POST /api/v1/hotels/:hotel_id/room_types returns 422 for invalid params" do
    # Missing required fields
    room_type_params = {
      name: "Incomplete Room Type"
      # Missing: code, base_price, standard_occupancy, max_occupancy
    }

    assert_no_difference('RoomType.count') do
      post "/api/v1/hotels/#{@hotel.id}/room_types",
           params: room_type_params.to_json,
           headers: @headers
    end

    assert_response :unprocessable_entity
    assert_json_response
  end

  test "POST /api/v1/hotels/:hotel_id/room_types returns 403 for housekeeping role" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)

    room_type_params = {
      name: "New Room Type",
      code: "NEW",
      base_price: 150.00,
      standard_occupancy: 2,
      max_occupancy: 3
    }

    post "/api/v1/hotels/#{@hotel.id}/room_types",
         params: room_type_params.to_json,
         headers: headers

    assert_response :forbidden
  end

  # Update tests
  test "PATCH /api/v1/hotels/:hotel_id/room_types/:id updates room type" do
    patch "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}",
          params: { name: "Updated Name", base_price: 299.99 }.to_json,
          headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_equal "Updated Name", data['name']
    assert_equal "299.99", data['base_price']

    @room_type.reload
    assert_equal "Updated Name", @room_type.name
    assert_equal 299.99, @room_type.base_price.to_f
  end

  test "PATCH /api/v1/hotels/:hotel_id/room_types/:id returns 422 for invalid params" do
    patch "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}",
          params: { max_occupancy: -1 }.to_json,
          headers: @headers

    assert_response :unprocessable_entity
    assert_json_response
  end

  test "PATCH /api/v1/hotels/:hotel_id/room_types/:id returns 404 for non-existent room type" do
    patch "/api/v1/hotels/#{@hotel.id}/room_types/999999",
          params: { name: "New Name" }.to_json,
          headers: @headers

    assert_response :not_found
  end

  # Delete tests
  test "DELETE /api/v1/hotels/:hotel_id/room_types/:id deletes room type without rooms" do
    deletable_room_type = create(:room_type, hotel: @hotel, account: @account)

    assert_difference('RoomType.count', -1) do
      delete "/api/v1/hotels/#{@hotel.id}/room_types/#{deletable_room_type.id}",
             headers: @headers
    end

    assert_response :no_content
  end

  test "DELETE /api/v1/hotels/:hotel_id/room_types/:id returns 409 conflict when room type has rooms" do
    # Create a room for this room type
    create(:room, hotel: @hotel, room_type: @room_type, account: @account)

    assert_no_difference('RoomType.count') do
      delete "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}",
             headers: @headers
    end

    assert_response :conflict
    assert_json_response
    assert_equal 'Conflict', json_response['error']
    assert json_response['message'].include?('existing rooms')
  end

  test "DELETE /api/v1/hotels/:hotel_id/room_types/:id returns 404 for non-existent room type" do
    delete "/api/v1/hotels/#{@hotel.id}/room_types/999999", headers: @headers

    assert_response :not_found
  end

  # Authorization tests
  test "returns 401 without API key" do
    get "/api/v1/hotels/#{@hotel.id}/room_types"

    assert_response :unauthorized
  end

  test "returns 404 for room type from different account (tenant isolation)" do
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)
    other_room_type = create(:room_type, hotel: other_hotel, account: other_account)

    # Try to access other account's hotel
    get "/api/v1/hotels/#{other_hotel.id}/room_types", headers: @headers

    assert_response :not_found
  end

  test "returns 404 for hotel from different account" do
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)

    get "/api/v1/hotels/#{other_hotel.id}/room_types", headers: @headers

    assert_response :not_found
  end

  # Role-based access tests
  test "front_desk role can view room types" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)

    get "/api/v1/hotels/#{@hotel.id}/room_types", headers: headers

    assert_response :success
  end

  test "owner role can create room types" do
    owner_agent = create_api_agent(@account, :owner)
    headers = api_headers_for(owner_agent)

    room_type_params = {
      name: "Owner Created Room",
      code: "OWN",
      base_price: 200.00,
      standard_occupancy: 2,
      max_occupancy: 4
    }

    post "/api/v1/hotels/#{@hotel.id}/room_types",
         params: room_type_params.to_json,
         headers: headers

    assert_response :created
  end

  test "admin role can update room types" do
    admin_agent = create_api_agent(@account, :admin)
    headers = api_headers_for(admin_agent)

    patch "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}",
          params: { name: "Admin Updated" }.to_json,
          headers: headers

    assert_response :success
  end
end
