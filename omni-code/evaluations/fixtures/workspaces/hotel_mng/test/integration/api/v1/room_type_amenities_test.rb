require "test_helper"

class Api::V1::RoomTypeAmenitiesTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, account: @account, hotel: @hotel)
    @amenity = create(:amenity, account: @account, name: "TV", category: "room", is_countable: true)
    @room_type_amenity = create(:room_type_amenity, 
                               account: @account,
                               room_type: @room_type,
                               amenity: @amenity,
                               quantity: 2)
    
    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities returns amenities" do
    create(:room_type_amenity, account: @account, room_type: @room_type,
           amenity: create(:amenity, account: @account, name: "Mini Bar"))
    
    get "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities", 
        headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data.length
    assert data.first['amenity'].present?
    assert_equal "TV", data.first['amenity']['name']
    assert_equal 2, data.first['quantity']
  end
  
  test "GET /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities filters by countable" do
    non_countable = create(:room_type_amenity, account: @account, room_type: @room_type,
                          amenity: create(:amenity, account: @account, is_countable: false))
    
    get "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities",
        params: { countable: "true" },
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert data.all? { |a| a['amenity']['is_countable'] == true }
  end
  
  test "GET /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities/:id returns details" do
    get "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities/#{@room_type_amenity.id}",
        headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @room_type_amenity.id, data['id']
    assert_equal 2, data['quantity']
    assert_equal "2 TVs", data['display_text']
  end
  
  test "POST /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities creates amenity" do
    new_amenity = create(:amenity, account: @account, name: "Safe", is_countable: false)
    
    params = {
      amenity_id: new_amenity.id,
      quantity: 1
    }
    
    assert_difference('RoomTypeAmenity.count') do
      post "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities",
           params: params.to_json,
           headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal new_amenity.id, data['amenity_id']
    assert_equal 1, data['quantity']
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities/:id updates quantity" do
    patch "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities/#{@room_type_amenity.id}",
          params: { quantity: 3 }.to_json,
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 3, data['quantity']
    assert_equal "3 TVs", data['display_text']
  end
  
  test "DELETE /api/v1/hotels/:hotel_id/room_types/:room_type_id/amenities/:id removes amenity" do
    assert_difference('RoomTypeAmenity.count', -1) do
      delete "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities/#{@room_type_amenity.id}",
             headers: @headers
    end
    
    assert_response :no_content
  end
  
  test "validates amenity uniqueness per room type" do
    params = {
      amenity_id: @amenity.id,
      quantity: 1
    }
    
    post "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities",
         params: params.to_json,
         headers: @headers
    
    assert_response :unprocessable_content
    assert_json_response
    assert json_response['errors'].present?
  end
  
  test "front_desk role can view but not modify room type amenities" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)
    
    # Can view
    get "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities", 
        headers: headers
    assert_response :success
    
    # Cannot create
    post "/api/v1/hotels/#{@hotel.id}/room_types/#{@room_type.id}/amenities",
         params: { amenity_id: @amenity.id }.to_json,
         headers: headers
    assert_response :forbidden
  end
  
  test "returns 404 for non-existent room type" do
    get "/api/v1/hotels/#{@hotel.id}/room_types/999999/amenities", headers: @headers
    assert_response :not_found
  end
end