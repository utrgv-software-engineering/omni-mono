require "test_helper"

class Api::V1::RoomsTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @rooms = create_list(:room, 5, hotel: @hotel, room_type: @room_type, account: @account)
    
    @api_agent = create_api_agent(@account, :front_desk)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms returns list of rooms" do
    get "/api/v1/hotels/#{@hotel.id}/rooms", headers: @headers
    
    assert_api_conform(status: 200)

    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 5, data.length
    assert data.first.key?('room_number')
    assert data.first.key?('status')
    assert data.first.key?('room_type')
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms filters by status" do
    @rooms.first.update!(status: 'maintenance')
    
    get "/api/v1/hotels/#{@hotel.id}/rooms", 
        params: { status: 'maintenance' },
        headers: @headers
    
    assert_api_conform(status: 200)

    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal 'maintenance', data.first['status']
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms filters by floor" do
    @rooms.first.update!(floor: 3)
    
    get "/api/v1/hotels/#{@hotel.id}/rooms", 
        params: { floor: 3 },
        headers: @headers
    
    assert_api_conform(status: 200)

    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal '3', data.first['floor']
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms supports pagination" do
    get "/api/v1/hotels/#{@hotel.id}/rooms", 
        params: { page: 1, per_page: 2 },
        headers: @headers
    
    assert_api_conform(status: 200)

    assert_response :success
    
    data = json_response['data']
    meta = json_response['meta']
    
    assert_equal 2, data.length
    assert_equal 1, meta['current_page']
    assert_equal 3, meta['total_pages']
    assert_equal 5, meta['total_count']
  end
  
  test "housekeeping role can view rooms" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)
    
    get "/api/v1/hotels/#{@hotel.id}/rooms", headers: headers
    
    assert_api_conform(status: 200)

    assert_response :success
    assert_json_response
  end
  
  test "GET /api/v1/hotels/:hotel_id/rooms returns 404 for non-existent hotel" do
    get "/api/v1/hotels/99999/rooms", headers: @headers
    
    assert_api_conform(status: 404)

    assert_response :not_found
    assert_json_response
    assert_equal 'Not Found', json_response['error']
  end
end