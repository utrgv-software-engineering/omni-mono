require "test_helper"

class Api::V1::HotelPoliciesTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @hotel_policy = create(:hotel_policy, 
                          account: @account,
                          hotel: @hotel,
                          policy_type: "check_in",
                          title: "Check-in Policy",
                          description: "Check-in at 3PM",
                          sort_order: 1,
                          is_active: true)
    
    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/policies returns hotel policies" do
    create(:hotel_policy, account: @account, hotel: @hotel, 
           policy_type: "check_out", title: "Check-out Policy")
    create(:hotel_policy, account: @account, hotel: @hotel,
           policy_type: "cancellation", title: "Cancellation Policy")
    
    get "/api/v1/hotels/#{@hotel.id}/policies", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 3, data.length
    # Verify all expected policies are present
    titles = data.map { |p| p['title'] }
    assert_includes titles, "Check-in Policy"
    assert_includes titles, "Check-out Policy"
    assert_includes titles, "Cancellation Policy"
  end
  
  test "GET /api/v1/hotels/:hotel_id/policies filters by policy type" do
    create(:hotel_policy, account: @account, hotel: @hotel,
           policy_type: "pet", title: "Pet Policy")
    create(:hotel_policy, account: @account, hotel: @hotel,
           policy_type: "smoking", title: "No Smoking")
    
    get "/api/v1/hotels/#{@hotel.id}/policies",
        params: { policy_type: "pet" },
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal "pet", data.first['policy_type']
  end
  
  test "GET /api/v1/hotels/:hotel_id/policies filters by active status" do
    inactive = create(:hotel_policy, account: @account, hotel: @hotel,
                     policy_type: "pet", is_active: false)
    
    get "/api/v1/hotels/#{@hotel.id}/policies",
        params: { active: "true" },
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert data.all? { |p| p['is_active'] == true }
  end
  
  test "GET /api/v1/hotels/:hotel_id/policies/:id returns policy details" do
    get "/api/v1/hotels/#{@hotel.id}/policies/#{@hotel_policy.id}", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @hotel_policy.id, data['id']
    assert_equal "check_in", data['policy_type']
    assert_equal "Check-in Policy", data['title']
    assert_equal "Check-in at 3PM", data['description']
  end
  
  test "POST /api/v1/hotels/:hotel_id/policies creates new policy" do
    params = {
      policy_type: "pet",
      title: "Pet Policy",
      description: "Pets welcome with $50 fee",
      sort_order: 5,
      is_active: true
    }
    
    assert_difference('HotelPolicy.count') do
      post "/api/v1/hotels/#{@hotel.id}/policies",
           params: params.to_json,
           headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "pet", data['policy_type']
    assert_equal "Pet Policy", data['title']
    assert_equal 5, data['sort_order']
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/policies/:id updates policy" do
    patch "/api/v1/hotels/#{@hotel.id}/policies/#{@hotel_policy.id}",
          params: { 
            description: "Check-in at 2PM for VIP guests",
            sort_order: 10 
          }.to_json,
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal "Check-in at 2PM for VIP guests", data['description']
    assert_equal 10, data['sort_order']
  end
  
  test "DELETE /api/v1/hotels/:hotel_id/policies/:id removes policy" do
    assert_difference('HotelPolicy.count', -1) do
      delete "/api/v1/hotels/#{@hotel.id}/policies/#{@hotel_policy.id}",
             headers: @headers
    end
    
    assert_response :no_content
  end
  
  test "POST /api/v1/hotels/:hotel_id/policies/:id/activate activates policy" do
    @hotel_policy.update!(is_active: false)
    
    post "/api/v1/hotels/#{@hotel.id}/policies/#{@hotel_policy.id}/activate",
         headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal true, data['is_active']
    
    @hotel_policy.reload
    assert @hotel_policy.is_active?
  end
  
  test "POST /api/v1/hotels/:hotel_id/policies/:id/deactivate deactivates policy" do
    post "/api/v1/hotels/#{@hotel.id}/policies/#{@hotel_policy.id}/deactivate",
         headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal false, data['is_active']
    
    @hotel_policy.reload
    assert_not @hotel_policy.is_active?
  end
  
  test "validates policy type" do
    params = {
      policy_type: "invalid_type",
      title: "Test",
      description: "Test"
    }
    
    post "/api/v1/hotels/#{@hotel.id}/policies",
         params: params.to_json,
         headers: @headers
    
    assert_response :unprocessable_content
    assert_json_response
    assert json_response['errors'].present?
  end
  
  test "front_desk role can view but not modify hotel policies" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)

    # Can view
    get "/api/v1/hotels/#{@hotel.id}/policies", headers: headers
    assert_response :success

    # Cannot create
    post "/api/v1/hotels/#{@hotel.id}/policies",
         params: { policy_type: "pet", title: "Test", description: "Test" }.to_json,
         headers: headers
    assert_response :forbidden
  end

  test "POST /api/v1/hotels/:hotel_id/policies/:id/activate returns 404 for non-existent policy" do
    post "/api/v1/hotels/#{@hotel.id}/policies/999999/activate",
         headers: @headers

    assert_response :not_found
  end

  test "POST /api/v1/hotels/:hotel_id/policies/:id/deactivate returns 404 for non-existent policy" do
    post "/api/v1/hotels/#{@hotel.id}/policies/999999/deactivate",
         headers: @headers

    assert_response :not_found
  end

  test "POST /api/v1/hotels/:hotel_id/policies/:id/activate returns 403 for front_desk role" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)
    @hotel_policy.update!(is_active: false)

    post "/api/v1/hotels/#{@hotel.id}/policies/#{@hotel_policy.id}/activate",
         headers: headers

    assert_response :forbidden
  end

  test "POST /api/v1/hotels/:hotel_id/policies/:id/deactivate returns 403 for front_desk role" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)

    post "/api/v1/hotels/#{@hotel.id}/policies/#{@hotel_policy.id}/deactivate",
         headers: headers

    assert_response :forbidden
  end

  test "returns 401 without API key" do
    get "/api/v1/hotels/#{@hotel.id}/policies"

    assert_response :unauthorized
  end

  test "returns 404 for policy from different account" do
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)
    other_policy = create(:hotel_policy, account: other_account, hotel: other_hotel)

    get "/api/v1/hotels/#{@hotel.id}/policies/#{other_policy.id}",
        headers: @headers

    assert_response :not_found
  end

  test "DELETE /api/v1/hotels/:hotel_id/policies/:id returns 404 for non-existent policy" do
    delete "/api/v1/hotels/#{@hotel.id}/policies/999999",
           headers: @headers

    assert_response :not_found
  end
end