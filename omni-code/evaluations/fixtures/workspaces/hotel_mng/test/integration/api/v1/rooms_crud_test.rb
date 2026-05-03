require "test_helper"

class Api::V1::RoomsCrudTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    
    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms returns list of rooms" do
    # Create additional rooms
    room2 = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    room3 = create(:room, hotel: @hotel, room_type: @room_type, account: @account, status: 'occupied')
    
    get "/api/v1/hotels/#{@hotel.id}/rooms", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 3, data.size
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms with status filter" do
    create(:room, hotel: @hotel, room_type: @room_type, account: @account, status: 'occupied')
    create(:room, hotel: @hotel, room_type: @room_type, account: @account, status: 'cleaning')
    
    get "/api/v1/hotels/#{@hotel.id}/rooms", params: { status: 'occupied' }, headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert data.all? { |r| r['status'] == 'occupied' }
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms/:id returns room details" do
    get "/api/v1/hotels/#{@hotel.id}/rooms/#{@room.id}", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @room.id, data['id']
    assert_equal @room.room_number, data['room_number']
    assert_equal @room.status, data['status']
  end
  
  test "POST /api/v1/hotels/:hotel_id/rooms creates a new room" do
    room_params = {
      room_type_id: @room_type.id,
      room_number: "201",
      floor: 2,  # Changed to integer
      status: "available",
      is_smoking: false
    }
    
    assert_difference('Room.count', 1) do
      post "/api/v1/hotels/#{@hotel.id}/rooms", 
           params: room_params.to_json, 
           headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "201", data['room_number']
    assert_equal "2", data['floor']  # Floor is stored as string in DB
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/rooms/:id updates room" do
    patch "/api/v1/hotels/#{@hotel.id}/rooms/#{@room.id}", 
          params: { status: "maintenance", notes: "Needs repair" }.to_json, 
          headers: @headers
    
    assert_response :success
    
    data = json_response['data']
    assert_equal "maintenance", data['status']
    assert_equal "Needs repair", data['notes']
  end
  
  test "housekeeping role can only update room status and last_cleaned_at" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)
    
    # Should be able to update status
    patch "/api/v1/hotels/#{@hotel.id}/rooms/#{@room.id}", 
          params: { status: "cleaning", room_number: "999" }.to_json, 
          headers: headers
    
    assert_response :success
    
    @room.reload
    assert_equal "cleaning", @room.status
    assert_not_equal "999", @room.room_number # room_number should not change
  end
  
  test "DELETE /api/v1/hotels/:hotel_id/rooms/:id deletes room without active reservations" do
    deletable_room = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    
    assert_difference('Room.count', -1) do
      delete "/api/v1/hotels/#{@hotel.id}/rooms/#{deletable_room.id}", headers: @headers
    end
    
    assert_response :no_content
  end
  
  test "DELETE /api/v1/hotels/:hotel_id/rooms/:id returns conflict for room with active reservations" do
    # Create an active reservation for the room
    guest = create(:guest)
    reservation = create(:reservation, 
                        hotel: @hotel, 
                        room: @room,
                        guest: guest, 
                        room_type: @room_type,
                        account: @account,
                        status: 'confirmed')
    
    delete "/api/v1/hotels/#{@hotel.id}/rooms/#{@room.id}", headers: @headers
    
    assert_response :conflict
    assert_json_response
    assert_equal 'Conflict', json_response['error']
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/rooms/:id/status updates room status" do
    patch "/api/v1/hotels/#{@hotel.id}/rooms/#{@room.id}/status", 
          params: { status: "occupied" }.to_json, 
          headers: @headers
    
    assert_response :success
    
    data = json_response['data']
    assert_equal "occupied", data['status']
    
    @room.reload
    assert_equal "occupied", @room.status
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/rooms/:id/status returns error for invalid status" do
    patch "/api/v1/hotels/#{@hotel.id}/rooms/#{@room.id}/status", 
          params: { status: "invalid_status" }.to_json, 
          headers: @headers
    
    assert_response :bad_request
    assert_json_response
    assert json_response['message'].include?('Invalid status')
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/rooms/:id/status sets last_cleaned_at when status is available" do
    # First set to cleaning
    @room.update!(status: 'cleaning', last_cleaned_at: nil)

    patch "/api/v1/hotels/#{@hotel.id}/rooms/#{@room.id}/status",
          params: { status: "available" }.to_json,
          headers: @headers

    assert_response :success

    @room.reload
    assert_equal "available", @room.status
    assert_not_nil @room.last_cleaned_at
    assert @room.last_cleaned_at > 1.minute.ago
  end

  test "returns 401 without API key" do
    get "/api/v1/hotels/#{@hotel.id}/rooms"

    assert_response :unauthorized
  end

  test "returns 404 for room from different account" do
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)
    other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
    other_room = create(:room, hotel: other_hotel, room_type: other_room_type, account: other_account)

    get "/api/v1/hotels/#{@hotel.id}/rooms/#{other_room.id}",
        headers: @headers

    assert_response :not_found
  end
end