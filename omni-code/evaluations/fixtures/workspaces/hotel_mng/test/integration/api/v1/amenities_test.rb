require "test_helper"

class Api::V1::AmenitiesTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @amenity = create(:amenity, account: @account, name: "WiFi", category: "hotel")
    @other_amenity = create(:amenity) # Different account
    
    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/amenities returns list of amenities" do
    create(:amenity, account: @account, name: "Pool", category: "hotel")
    create(:amenity, account: @account, name: "Gym", category: "wellness")
    
    get "/api/v1/amenities", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert data.is_a?(Array)
    assert_equal 3, data.length
    assert data.all? { |a| a['id'].present? }
  end
  
  test "GET /api/v1/amenities filters by category" do
    create(:amenity, account: @account, name: "Pool", category: "hotel")
    create(:amenity, account: @account, name: "Gym", category: "wellness")
    
    get "/api/v1/amenities", params: { category: "hotel" }, headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data.length
    assert data.all? { |a| a['category'] == 'hotel' }
  end
  
  test "GET /api/v1/amenities supports search" do
    create(:amenity, account: @account, name: "Swimming Pool", category: "hotel")
    create(:amenity, account: @account, name: "Gym", category: "wellness")
    
    get "/api/v1/amenities", params: { search: "Pool" }, headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal "Swimming Pool", data.first['name']
  end
  
  test "GET /api/v1/amenities/:id returns amenity details" do
    get "/api/v1/amenities/#{@amenity.id}", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @amenity.id, data['id']
    assert_equal "WiFi", data['name']
    assert_equal "hotel", data['category']
  end
  
  test "GET /api/v1/amenities/:id returns 404 for amenity in different account" do
    get "/api/v1/amenities/#{@other_amenity.id}", headers: @headers
    
    assert_response :not_found
    assert_json_response
    assert_equal 'Not Found', json_response['error']
  end
  
  test "POST /api/v1/amenities creates new amenity" do
    amenity_params = {
      name: "Spa",
      category: "wellness",
      description: "Full service spa",
      icon: "spa",
      is_countable: false
    }
    
    assert_difference('Amenity.count') do
      post "/api/v1/amenities", params: amenity_params.to_json, headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "Spa", data['name']
    assert_equal "wellness", data['category']
    assert_equal "Full service spa", data['description']
  end
  
  test "POST /api/v1/amenities returns validation errors" do
    amenity_params = {
      name: "",
      category: "invalid"
    }
    
    post "/api/v1/amenities", params: amenity_params.to_json, headers: @headers
    
    assert_response :unprocessable_content
    assert_json_response
    assert json_response['errors'].present?
  end
  
  test "PATCH /api/v1/amenities/:id updates amenity" do
    patch "/api/v1/amenities/#{@amenity.id}", 
          params: { description: "High-speed WiFi" }.to_json,
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal "High-speed WiFi", data['description']
    
    @amenity.reload
    assert_equal "High-speed WiFi", @amenity.description
  end
  
  test "DELETE /api/v1/amenities/:id deletes amenity" do
    assert_difference('Amenity.count', -1) do
      delete "/api/v1/amenities/#{@amenity.id}", headers: @headers
    end
    
    assert_response :no_content
  end
  
  test "DELETE /api/v1/amenities/:id returns conflict if amenity in use" do
    hotel_amenity = create(:hotel_amenity, amenity: @amenity, hotel: create(:hotel, account: @account))
    
    assert_no_difference('Amenity.count') do
      delete "/api/v1/amenities/#{@amenity.id}", headers: @headers
    end
    
    assert_response :conflict
    assert_json_response
    assert_equal 'Conflict', json_response['error']
  end
  
  test "front_desk role cannot create amenities" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)
    
    post "/api/v1/amenities", 
         params: { name: "Test", category: "hotel" }.to_json,
         headers: headers
    
    assert_response :forbidden
  end
  
  test "requires valid API key" do
    get "/api/v1/amenities"
    assert_response :unauthorized
  end
end