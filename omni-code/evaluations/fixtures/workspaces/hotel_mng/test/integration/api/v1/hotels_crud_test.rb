require "test_helper"

class Api::V1::HotelsCrudTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @other_account = create(:account)
    @other_hotel = create(:hotel, account: @other_account)
    
    @api_agent = create_api_agent(@account, :admin)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels returns list of hotels for account" do
    # Create another hotel for the same account
    hotel2 = create(:hotel, account: @account)
    
    get "/api/v1/hotels", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data.size
    assert data.any? { |h| h['id'] == @hotel.id }
    assert data.any? { |h| h['id'] == hotel2.id }
    
    # Should not include hotel from other account
    assert data.none? { |h| h['id'] == @other_hotel.id }
  end
  
  test "GET /api/v1/hotels with search filter" do
    special_hotel = create(:hotel, account: @account, name: "Special Resort")
    
    get "/api/v1/hotels", params: { search: "Special" }, headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.size
    assert_equal special_hotel.id, data[0]['id']
  end
  
  test "POST /api/v1/hotels creates a new hotel" do
    hotel_params = {
      name: "New Hotel",
      subdomain: "newhotel",
      address: "123 New Street",
      city: "New York",
      state: "NY",
      country: "US",
      postal_code: "10001",
      phone: "555-1234",
      email: "info@newhotel.com"
    }
    
    assert_difference('Hotel.count', 1) do
      post "/api/v1/hotels", 
           params: hotel_params.to_json, 
           headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "New Hotel", data['name']
    assert_equal "newhotel", data['subdomain']
  end
  
  test "POST /api/v1/hotels returns validation errors for invalid data" do
    invalid_params = {
      name: "", # Invalid: blank name
      email: "invalid-email" # Invalid: bad format
    }
    
    post "/api/v1/hotels", 
         params: invalid_params.to_json, 
         headers: @headers
    
    assert_response :unprocessable_content
    assert_json_response
    assert json_response['errors'].present?
  end
  
  test "PATCH /api/v1/hotels/:id updates hotel" do
    patch "/api/v1/hotels/#{@hotel.id}", 
          params: { name: "Updated Hotel Name" }.to_json, 
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal "Updated Hotel Name", data['name']
    
    @hotel.reload
    assert_equal "Updated Hotel Name", @hotel.name
  end
  
  test "PATCH /api/v1/hotels/:id returns 404 for hotel in different account" do
    patch "/api/v1/hotels/#{@other_hotel.id}", 
          params: { name: "Hacked Name" }.to_json, 
          headers: @headers
    
    assert_response :not_found
  end
  
  test "DELETE /api/v1/hotels/:id deletes hotel without reservations" do
    deletable_hotel = create(:hotel, account: @account)
    
    assert_difference('Hotel.count', -1) do
      delete "/api/v1/hotels/#{deletable_hotel.id}", headers: @headers
    end
    
    assert_response :no_content
  end
  
  test "DELETE /api/v1/hotels/:id returns conflict for hotel with reservations" do
    # Create a reservation for the hotel
    guest = create(:guest)
    room_type = create(:room_type, hotel: @hotel, account: @account)
    reservation = create(:reservation, 
                        hotel: @hotel, 
                        guest: guest, 
                        room_type: room_type,
                        account: @account)
    
    delete "/api/v1/hotels/#{@hotel.id}", headers: @headers
    
    assert_response :conflict
    assert_json_response
    assert_equal 'Conflict', json_response['error']
  end
  
  test "owner role can perform all hotel operations" do
    owner_agent = create_api_agent(@account, :owner)
    headers = api_headers_for(owner_agent)
    
    # Test all operations
    get "/api/v1/hotels", headers: headers
    assert_response :success
    
    post "/api/v1/hotels", 
         params: { 
           name: "Owner Hotel", 
           subdomain: "ownerhotel",
           address: "456 Owner St",
           city: "San Francisco",
           state: "CA",
           country: "US",
           postal_code: "94102",
           phone: "555-9999",
           email: "owner@hotel.com"
         }.to_json, 
         headers: headers
    assert_response :created
    
    new_hotel_id = json_response['data']['id']
    
    patch "/api/v1/hotels/#{new_hotel_id}", 
          params: { name: "Updated Owner Hotel" }.to_json, 
          headers: headers
    assert_response :success
    
    delete "/api/v1/hotels/#{new_hotel_id}", headers: headers
    assert_response :no_content
  end
  
  test "front_desk role cannot create or delete hotels" do
    desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(desk_agent)
    
    # Can view
    get "/api/v1/hotels/#{@hotel.id}", headers: headers
    assert_response :success
    
    # Cannot create
    post "/api/v1/hotels", 
         params: { 
           name: "Desk Hotel", 
           subdomain: "deskhotel",
           address: "789 Desk Ave",
           city: "San Francisco",
           state: "CA",
           country: "US",
           postal_code: "94103",
           phone: "555-7777",
           email: "desk@hotel.com"
         }.to_json, 
         headers: headers
    assert_response :forbidden
    
    # Cannot delete
    delete "/api/v1/hotels/#{@hotel.id}", headers: headers
    assert_response :forbidden
  end
end