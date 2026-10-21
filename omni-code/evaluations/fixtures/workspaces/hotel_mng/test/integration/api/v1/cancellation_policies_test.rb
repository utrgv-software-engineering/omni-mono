require "test_helper"

class Api::V1::CancellationPoliciesTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @account_policy = create(:cancellation_policy,
                            account: @account,
                            hotel: nil,
                            name: "Flexible",
                            code: "FLEX",
                            cancellation_type: "flexible",
                            free_cancellation_hours: 24,
                            is_refundable: true,
                            is_default: true)
    @hotel_policy = create(:cancellation_policy,
                          account: @account,
                          hotel: @hotel,
                          name: "Strict Hotel",
                          code: "STRICT-H",
                          cancellation_type: "strict",
                          penalty_percentage: 50,
                          is_refundable: false)
    
    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/cancellation_policies returns all policies" do
    get "/api/v1/cancellation_policies", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data.length
    assert data.any? { |p| p['scope'] == 'account_wide' }
    assert data.any? { |p| p['scope'] == 'hotel_specific' }
  end
  
  test "GET /api/v1/cancellation_policies filters by scope" do
    get "/api/v1/cancellation_policies",
        params: { scope: "account_wide" },
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal "account_wide", data.first['scope']
    assert_nil data.first['hotel_id']
  end
  
  test "GET /api/v1/cancellation_policies filters by cancellation type" do
    get "/api/v1/cancellation_policies",
        params: { cancellation_type: "flexible" },
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal "flexible", data.first['cancellation_type']
  end
  
  test "GET /api/v1/cancellation_policies filters by refundable" do
    get "/api/v1/cancellation_policies",
        params: { refundable: "true" },
        headers: @headers
    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal true, data.first['is_refundable']
  end
  
  test "GET /api/v1/hotels/:hotel_id/cancellation_policies returns hotel and account policies" do
    get "/api/v1/hotels/#{@hotel.id}/cancellation_policies", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data.length
    # Should include both account-wide and hotel-specific policies
    assert data.any? { |p| p['hotel_id'].nil? }
    assert data.any? { |p| p['hotel_id'] == @hotel.id }
  end
  
  test "GET /api/v1/cancellation_policies/:id returns policy details" do
    get "/api/v1/cancellation_policies/#{@account_policy.id}", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @account_policy.id, data['id']
    assert_equal "Flexible", data['name']
    assert_equal "FLEX", data['code']
    assert_equal 24, data['free_cancellation_hours']
  end
  
  test "POST /api/v1/cancellation_policies creates account-wide policy" do
    params = {
      name: "Moderate",
      code: "MOD",
      cancellation_type: "moderate",
      description: "Moderate cancellation policy",
      is_refundable: true,
      free_cancellation_hours: 72,
      penalty_nights: 1,
      no_show_penalty: 100
    }
    
    assert_difference('CancellationPolicy.count') do
      post "/api/v1/cancellation_policies",
           params: params.to_json,
           headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "Moderate", data['name']
    assert_equal "moderate", data['cancellation_type']
    assert_equal "account_wide", data['scope']
    assert_nil data['hotel_id']
  end
  
  test "POST /api/v1/hotels/:hotel_id/cancellation_policies creates hotel-specific policy" do
    params = {
      name: "Hotel Non-Refundable",
      code: "NR-H",
      cancellation_type: "non_refundable",
      description: "Non-refundable rate",
      is_refundable: false
    }
    
    assert_difference('CancellationPolicy.count') do
      post "/api/v1/hotels/#{@hotel.id}/cancellation_policies",
           params: params.to_json,
           headers: @headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal "Hotel Non-Refundable", data['name']
    assert_equal @hotel.id, data['hotel_id']
    assert_equal "hotel_specific", data['scope']
  end
  
  test "PATCH /api/v1/cancellation_policies/:id updates policy" do
    patch "/api/v1/cancellation_policies/#{@account_policy.id}",
          params: {
            description: "Updated flexible policy",
            free_cancellation_hours: 48
          }.to_json,
          headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal "Updated flexible policy", data['description']
    assert_equal 48, data['free_cancellation_hours']
  end
  
  test "DELETE /api/v1/cancellation_policies/:id removes policy" do
    assert_difference('CancellationPolicy.count', -1) do
      delete "/api/v1/cancellation_policies/#{@hotel_policy.id}",
             headers: @headers
    end
    
    assert_response :no_content
  end
  
  test "POST /api/v1/cancellation_policies/:id/make_default sets policy as default" do
    new_policy = create(:cancellation_policy, account: @account, is_default: false)
    
    post "/api/v1/cancellation_policies/#{new_policy.id}/make_default",
         headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal true, data['is_default']
    
    # Previous default should no longer be default
    @account_policy.reload
    assert_not @account_policy.is_default?
  end
  
  test "POST /api/v1/cancellation_policies/calculate_fee calculates cancellation fee" do
    reservation = create(:reservation, 
                        account: @account,
                        check_in_date: 7.days.from_now,
                        check_out_date: 10.days.from_now,
                        total_amount: 300,
                        paid_amount: 300)
    
    params = {
      policy_id: @account_policy.id,
      reservation_id: reservation.id,
      cancellation_date: Date.current.to_s
    }
    
    post "/api/v1/cancellation_policies/calculate_fee",
         params: params.to_json,
         headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert data['cancellation_fee'].present?
    assert data['no_show_fee'].present?
    assert_equal "flexible", data['policy_type']
    assert_equal true, data['is_refundable']
  end
  
  test "validates cancellation type" do
    params = {
      name: "Invalid",
      code: "INV",
      cancellation_type: "invalid_type"
    }
    
    post "/api/v1/cancellation_policies",
         params: params.to_json,
         headers: @headers
    
    assert_response :unprocessable_content
    assert_json_response
    assert json_response['errors'].present?
  end
  
  test "front_desk role cannot create cancellation policies" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)

    post "/api/v1/cancellation_policies",
         params: { name: "Test", code: "TEST", cancellation_type: "flexible" }.to_json,
         headers: headers

    assert_response :forbidden
  end

  test "returns 401 without API key" do
    get "/api/v1/cancellation_policies"

    assert_response :unauthorized
  end

  test "returns 404 for policy from different account" do
    other_account = create(:account)
    other_policy = create(:cancellation_policy, account: other_account)

    get "/api/v1/cancellation_policies/#{other_policy.id}",
        headers: @headers

    assert_response :not_found
  end
end