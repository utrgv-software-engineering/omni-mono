require "test_helper"

class Api::V1::HotelsTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @other_hotel = create(:hotel) # Different account
    
    @api_agent = create_api_agent(@account, :front_desk)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels/:id returns hotel details with valid API key" do
    get "/api/v1/hotels/#{@hotel.id}", headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel.id, data['id']
    assert_equal @hotel.name, data['name']
    assert_equal @hotel.email, data['email']
  end
  
  test "GET /api/v1/hotels/:id returns 404 for hotel in different account" do
    get "/api/v1/hotels/#{@other_hotel.id}", headers: @headers
    
    assert_api_conform(status: 404)

    
    assert_response :not_found
    assert_json_response
    assert_equal 'Not Found', json_response['error']
  end
  
  test "GET /api/v1/hotels/:id returns 401 without API key" do
    get "/api/v1/hotels/#{@hotel.id}"
    
    assert_response :unauthorized
    assert_json_response
    assert_equal 'Unauthorized', json_response['error']
  end
  
  test "GET /api/v1/hotels/:id returns 401 with invalid API key" do
    headers = @headers.merge('X-API-Key' => 'invalid_key')
    get "/api/v1/hotels/#{@hotel.id}", headers: headers
    
    assert_response :unauthorized
    assert_json_response
    assert_equal 'Unauthorized', json_response['error']
  end
  
  test "housekeeping role can access hotel details" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)
    
    get "/api/v1/hotels/#{@hotel.id}", headers: headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    assert_json_response
  end
  
  test "API key from non-AI agent user is rejected" do
    # Create regular user with API key (shouldn't normally happen)
    regular_user = create(:user, is_ai_agent: false)
    account_user = AccountUser.create!(user: regular_user, account: @account)
    account_user.update!(api_key: 'ak_test_regular')
    
    headers = { 'X-API-Key' => 'ak_test_regular' }
    get "/api/v1/hotels/#{@hotel.id}", headers: headers
    
    assert_response :unauthorized
  end
  
  test "GET /api/v1/hotels/:id with include=amenities returns hotel with amenities" do
    # Create amenities for the hotel
    amenity1 = create(:amenity, account: @account, name: "Swimming Pool")
    amenity2 = create(:amenity, account: @account, name: "Gym")
    create(:hotel_amenity, hotel: @hotel, amenity: amenity1, account: @account, is_available: true)
    create(:hotel_amenity, hotel: @hotel, amenity: amenity2, account: @account, is_available: true)
    
    get "/api/v1/hotels/#{@hotel.id}?include=amenities", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel.id, data['id']
    assert data.key?('hotel_amenities'), "Response should include hotel_amenities"
    assert_equal 2, data['hotel_amenities'].length
    
    # Check that amenity details are included
    amenity_names = data['hotel_amenities'].map { |ha| ha['amenity']['name'] }
    assert_includes amenity_names, "Swimming Pool"
    assert_includes amenity_names, "Gym"
  end
  
  test "GET /api/v1/hotels/:id with include=policies returns hotel with policies" do
    # Create policies for the hotel
    create(:hotel_policy, hotel: @hotel, account: @account, title: "Check-in Policy", is_active: true)
    create(:hotel_policy, hotel: @hotel, account: @account, title: "Pet Policy", is_active: true)
    create(:hotel_policy, hotel: @hotel, account: @account, title: "Old Policy", is_active: false)
    
    get "/api/v1/hotels/#{@hotel.id}?include=policies", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel.id, data['id']
    assert data.key?('hotel_policies'), "Response should include hotel_policies"
    # Only active policies should be included
    assert_equal 2, data['hotel_policies'].length
    
    policy_titles = data['hotel_policies'].map { |p| p['title'] }
    assert_includes policy_titles, "Check-in Policy"
    assert_includes policy_titles, "Pet Policy"
    refute_includes policy_titles, "Old Policy"
  end
  
  test "GET /api/v1/hotels/:id with include=services returns hotel with services" do
    # Create services for the hotel
    create(:service, hotel: @hotel, account: @account, name: "Room Service", is_active: true)
    create(:service, hotel: @hotel, account: @account, name: "Spa Treatment", is_active: true)
    create(:service, hotel: @hotel, account: @account, name: "Discontinued Service", is_active: false)
    
    get "/api/v1/hotels/#{@hotel.id}?include=services", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel.id, data['id']
    assert data.key?('services'), "Response should include services"
    # Only active services should be included
    assert_equal 2, data['services'].length
    
    service_names = data['services'].map { |s| s['name'] }
    assert_includes service_names, "Room Service"
    assert_includes service_names, "Spa Treatment"
    refute_includes service_names, "Discontinued Service"
  end
  
  test "GET /api/v1/hotels/:id with include=all returns hotel with all associations" do
    # Create associated data
    amenity = create(:amenity, account: @account, name: "WiFi")
    create(:hotel_amenity, hotel: @hotel, amenity: amenity, account: @account, is_available: true)
    create(:hotel_policy, hotel: @hotel, account: @account, title: "Cancellation Policy", is_active: true)
    create(:service, hotel: @hotel, account: @account, name: "Laundry", is_active: true)
    
    get "/api/v1/hotels/#{@hotel.id}?include=all", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel.id, data['id']
    assert data.key?('hotel_amenities'), "Response should include hotel_amenities"
    assert data.key?('hotel_policies'), "Response should include hotel_policies"  
    assert data.key?('services'), "Response should include services"
    
    # Verify each has at least one item
    assert_equal 1, data['hotel_amenities'].length
    assert_equal 1, data['hotel_policies'].length
    assert_equal 1, data['services'].length
  end
  
  test "GET /api/v1/hotels/:id with multiple includes returns hotel with requested associations" do
    # Create associated data
    amenity = create(:amenity, account: @account, name: "Parking")
    create(:hotel_amenity, hotel: @hotel, amenity: amenity, account: @account, is_available: true)
    create(:service, hotel: @hotel, account: @account, name: "Valet", is_active: true)
    
    get "/api/v1/hotels/#{@hotel.id}?include=amenities,services", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel.id, data['id']
    assert data.key?('hotel_amenities'), "Response should include hotel_amenities"
    assert data.key?('services'), "Response should include services"
    refute data.key?('hotel_policies'), "Response should not include hotel_policies"
  end
  
  test "GET /api/v1/hotels/:id without include parameter returns basic hotel data" do
    # Create associated data that should NOT be included
    amenity = create(:amenity, account: @account)
    create(:hotel_amenity, hotel: @hotel, amenity: amenity, account: @account)
    create(:hotel_policy, hotel: @hotel, account: @account)
    create(:service, hotel: @hotel, account: @account)
    
    get "/api/v1/hotels/#{@hotel.id}", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel.id, data['id']
    assert_equal @hotel.name, data['name']
    
    # Should not include associations
    refute data.key?('hotel_amenities'), "Response should not include hotel_amenities"
    refute data.key?('hotel_policies'), "Response should not include hotel_policies"
    refute data.key?('services'), "Response should not include services"
  end
end