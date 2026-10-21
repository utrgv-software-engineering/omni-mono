require "test_helper"

class Api::V1::GuestsCrudTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @guest = create(:guest, account: @account)
    @guest2 = create(:guest, account: @account, vip_status: true)
    
    @api_agent = create_api_agent(@account, :front_desk)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/guests returns list of guests" do
    get "/api/v1/guests", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert data.size >= 2
    
    # Check pagination meta
    assert json_response['meta'].present?
    assert json_response['meta']['total_count'] >= 2
  end
  
  test "GET /api/v1/guests with search filter" do
    special_guest = create(:guest, account: @account, first_name: "John", last_name: "Doe", email: "john.doe@example.com")
    
    get "/api/v1/guests", params: { search: "john" }, headers: @headers
    
    assert_response :success
    data = json_response['data']
    
    assert data.any? { |g| g['id'] == special_guest.id }
  end
  
  test "GET /api/v1/guests with vip_status filter" do
    get "/api/v1/guests", params: { vip_status: true }, headers: @headers
    
    assert_response :success
    data = json_response['data']
    
    assert data.all? { |g| g['vip_status'] == true }
    assert data.any? { |g| g['id'] == @guest2.id }
  end
  
  test "GET /api/v1/guests/:id returns guest details" do
    get "/api/v1/guests/#{@guest.id}", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @guest.id, data['id']
    assert_equal @guest.email, data['email']
    assert_equal @guest.full_name, data['full_name']
  end
  
  test "POST /api/v1/guests creates a new guest" do
    guest_params = {
      first_name: "New",
      last_name: "Guest",
      email: "newguest@example.com",
      phone: "555-0123",
      address: "123 Main St",
      city: "New York",
      state: "NY",
      country: "US",
      postal_code: "10001"
    }
    
    assert_difference('Guest.count', 1) do
      post "/api/v1/guests", params: guest_params.to_json, headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "New", data['first_name']
    assert_equal "Guest", data['last_name']
    assert_equal "newguest@example.com", data['email']
  end
  
  test "POST /api/v1/guests creates guest even with duplicate email" do
    # Note: Guest model doesn't enforce email uniqueness per account
    existing_guest = create(:guest, account: @account, email: "existing@example.com")
    
    guest_params = {
      first_name: "Another",
      last_name: "Guest",
      email: "existing@example.com" # Duplicate email allowed
    }
    
    post "/api/v1/guests", params: guest_params.to_json, headers: @headers
    
    assert_response :created
    assert_json_response
  end
  
  test "PATCH /api/v1/guests/:id updates guest" do
    patch "/api/v1/guests/#{@guest.id}", 
          params: { phone: "555-9999", notes: "VIP customer" }.to_json, 
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal "555-9999", data['phone']
    assert_equal "VIP customer", data['notes']
    
    @guest.reload
    assert_equal "555-9999", @guest.phone
  end
  
  test "DELETE /api/v1/guests/:id requires manager role" do
    # Create a manager agent since destroy requires manager_or_above
    manager_agent = create_api_agent(@account, :hotel_manager)
    manager_headers = api_headers_for(manager_agent)
    
    deletable_guest = create(:guest, account: @account)
    
    assert_difference('Guest.count', -1) do
      delete "/api/v1/guests/#{deletable_guest.id}", headers: manager_headers
    end
    
    assert_response :no_content
  end
  
  test "DELETE /api/v1/guests/:id returns conflict for guest with reservations" do
    # Create a manager agent since destroy requires manager_or_above
    manager_agent = create_api_agent(@account, :hotel_manager)
    manager_headers = api_headers_for(manager_agent)
    
    # Create a reservation for the guest
    hotel = create(:hotel, account: @account)
    room_type = create(:room_type, hotel: hotel, account: @account)
    reservation = create(:reservation, 
                        hotel: hotel, 
                        guest: @guest, 
                        room_type: room_type,
                        account: @account)
    
    delete "/api/v1/guests/#{@guest.id}", headers: manager_headers
    
    assert_response :conflict
    assert_json_response
    assert_equal 'Conflict', json_response['error']
    assert json_response['message'].include?('existing reservations')
  end
  
  test "PATCH /api/v1/guests/:id/vip_status updates VIP status" do
    patch "/api/v1/guests/#{@guest.id}/vip_status", 
          params: { vip_status: true }.to_json, 
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal true, data['vip_status']
    
    @guest.reload
    assert @guest.vip_status
  end
  
  test "PATCH /api/v1/guests/:id/vip_status returns error without vip_status param" do
    patch "/api/v1/guests/#{@guest.id}/vip_status", 
          params: {}.to_json, 
          headers: @headers
    
    assert_response :bad_request
    assert_json_response
    assert json_response['message'].include?('VIP status parameter is required')
  end
  
  test "housekeeping role cannot access guest endpoints" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)
    
    get "/api/v1/guests", headers: headers
    assert_response :forbidden
    
    get "/api/v1/guests/#{@guest.id}", headers: headers
    assert_response :forbidden
    
    post "/api/v1/guests", 
         params: { first_name: "Test", last_name: "Guest", email: "test@example.com" }.to_json, 
         headers: headers
    assert_response :forbidden
  end
  
  test "front_desk role can manage guests" do
    desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(desk_agent)
    
    # Can list
    get "/api/v1/guests", headers: headers
    assert_response :success
    
    # Can create
    post "/api/v1/guests", 
         params: { first_name: "Desk", last_name: "Guest", email: "desk@example.com" }.to_json, 
         headers: headers
    assert_response :created
    
    new_guest_id = json_response['data']['id']
    
    # Can update
    patch "/api/v1/guests/#{new_guest_id}", 
          params: { phone: "555-1111" }.to_json, 
          headers: headers
    assert_response :success
    
    # Can update VIP status
    patch "/api/v1/guests/#{new_guest_id}/vip_status",
          params: { vip_status: true }.to_json,
          headers: headers
    assert_response :success
  end

  test "returns 401 without API key" do
    get "/api/v1/guests"

    assert_response :unauthorized
  end

  test "returns 404 for guest from different account" do
    other_account = create(:account)
    other_guest = create(:guest, account: other_account)

    get "/api/v1/guests/#{other_guest.id}",
        headers: @headers

    assert_response :not_found
  end
end