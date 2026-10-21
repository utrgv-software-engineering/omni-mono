require "test_helper"

class Api::V1::HotelAmenitiesTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @amenity = create(:amenity, account: @account, name: "Pool", category: "hotel")
    @hotel_amenity = create(:hotel_amenity, 
                           account: @account,
                           hotel: @hotel, 
                           amenity: @amenity,
                           is_available: true,
                           is_free: false,
                           additional_cost: 10.00)
    
    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/amenities returns hotel amenities" do
    create(:hotel_amenity, account: @account, hotel: @hotel, 
           amenity: create(:amenity, account: @account, name: "Gym"))
    
    get "/api/v1/hotels/#{@hotel.id}/amenities", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data.length
    assert data.first['amenity'].present?
    assert_equal "Pool", data.first['amenity']['name']
  end
  
  test "GET /api/v1/hotels/:hotel_id/amenities filters by availability" do
    unavailable = create(:hotel_amenity, account: @account, hotel: @hotel, 
                        amenity: create(:amenity, account: @account),
                        is_available: false)
    
    get "/api/v1/hotels/#{@hotel.id}/amenities", 
        params: { available: "true" }, 
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert data.all? { |a| a['is_available'] == true }
  end
  
  test "GET /api/v1/hotels/:hotel_id/amenities filters by free/paid" do
    free_amenity = create(:hotel_amenity, account: @account, hotel: @hotel, 
                         amenity: create(:amenity, account: @account),
                         is_free: true)
    
    get "/api/v1/hotels/#{@hotel.id}/amenities", 
        params: { free: "false" }, 
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal false, data.first['is_free']
    assert_equal "10.0", data.first['additional_cost']
  end
  
  test "GET /api/v1/hotels/:hotel_id/amenities/:id returns amenity details" do
    get "/api/v1/hotels/#{@hotel.id}/amenities/#{@hotel_amenity.id}", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel_amenity.id, data['id']
    assert_equal false, data['is_free']
    assert_equal "10.0", data['additional_cost']
  end
  
  test "POST /api/v1/hotels/:hotel_id/amenities creates hotel amenity" do
    new_amenity = create(:amenity, account: @account, name: "Spa")
    
    params = {
      amenity_id: new_amenity.id,
      is_available: true,
      is_free: false,
      additional_cost: 50.00,
      location: "Floor 2",
      operating_hours: "9AM-9PM",
      notes: "Reservation required"
    }
    
    assert_difference('HotelAmenity.count') do
      post "/api/v1/hotels/#{@hotel.id}/amenities", 
           params: params.to_json, 
           headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "50.0", data['additional_cost']
    assert_equal "Floor 2", data['location']
    assert_equal "9AM-9PM", data['operating_hours']
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/amenities/:id updates hotel amenity" do
    patch "/api/v1/hotels/#{@hotel.id}/amenities/#{@hotel_amenity.id}",
          params: { 
            is_free: true, 
            additional_cost: 0,
            operating_hours: "24/7" 
          }.to_json,
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal true, data['is_free']
    assert_equal "0.0", data['additional_cost']
    assert_equal "24/7", data['operating_hours']
  end
  
  test "DELETE /api/v1/hotels/:hotel_id/amenities/:id removes hotel amenity" do
    assert_difference('HotelAmenity.unscoped.count', -1) do
      delete "/api/v1/hotels/#{@hotel.id}/amenities/#{@hotel_amenity.id}", 
             headers: @headers
    end
    
    assert_response :no_content
  end
  
  test "front_desk role can view but not modify hotel amenities" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)
    
    # Can view
    get "/api/v1/hotels/#{@hotel.id}/amenities", headers: headers
    assert_response :success
    
    # Cannot create
    post "/api/v1/hotels/#{@hotel.id}/amenities",
         params: { amenity_id: @amenity.id }.to_json,
         headers: headers
    assert_response :forbidden
  end
  
  test "returns 404 for non-existent hotel" do
    get "/api/v1/hotels/999999/amenities", headers: @headers
    assert_response :not_found
  end
end