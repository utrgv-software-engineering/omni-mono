require "test_helper"

class Api::V1::MaintenanceRequestsTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @other_account = create(:account)
    @other_hotel = create(:hotel, account: @other_account)
    
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    @room2 = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    
    # Create a maintenance user for assignment
    @maintenance_user = create(:user)
    @maintenance_account_user = AccountUser.create!(account: @account, user: @maintenance_user)
    @maintenance_account_user.add_role(:front_desk)
    
    # Create reporter user
    @reporter_user = create(:user)
    @reporter_account_user = AccountUser.create!(account: @account, user: @reporter_user)
    @reporter_account_user.add_role(:front_desk)
    
    # Create default maintenance request
    @maintenance_request = create(:maintenance_request,
                     account: @account,
                     hotel: @hotel,
                     room: @room,
                     reported_by: @reporter_user,
                     assigned_to: @maintenance_user)
    
    # Create API agents with different roles
    @admin_agent = create_api_agent(@account, :admin)
    @admin_headers = api_headers_for(@admin_agent)
    
    @front_desk_agent = create_api_agent(@account, :front_desk)
    @front_desk_headers = api_headers_for(@front_desk_agent)
    
    @housekeeping_agent = create_api_agent(@account, :housekeeping)
    @housekeeping_headers = api_headers_for(@housekeeping_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/maintenance_requests returns list of requests" do
    # Create additional requests
    request2 = create(:maintenance_request, :critical, account: @account, hotel: @hotel, room: @room2, reported_by: @reporter_user)
    request3 = create(:maintenance_request, :completed, account: @account, hotel: @hotel, room: @room, reported_by: @reporter_user)
    
    # Request from different hotel shouldn't appear
    other_request = create(:maintenance_request, account: @other_account, hotel: @other_hotel, reported_by: @reporter_user)
    
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests", headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 3, data.size
    
    request_ids = data.map { |r| r['id'] }
    assert_includes request_ids, @maintenance_request.id
    assert_includes request_ids, request2.id
    assert_includes request_ids, request3.id
    assert_not_includes request_ids, other_request.id
    
    # Check pagination meta
    assert json_response['meta'].present?
    assert_equal 3, json_response['meta']['total_count']
  end
  
  test "GET /api/v1/hotels/:hotel_id/maintenance_requests with status filter" do
    open_request = create(:maintenance_request, status: 'open', account: @account, hotel: @hotel, room: @room2, reported_by: @reporter_user)
    completed_request = create(:maintenance_request, :completed, account: @account, hotel: @hotel, room: @room2, reported_by: @reporter_user)
    
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests",
        params: { status: 'open' },
        headers: @housekeeping_headers
    
    assert_response :success
    data = json_response['data']
    
    assert_equal 2, data.size # @maintenance_request (default open) and open_request
    assert data.all? { |r| r['status'] == 'open' }
  end
  
  test "GET /api/v1/hotels/:hotel_id/maintenance_requests with priority filter" do
    critical_request = create(:maintenance_request, :critical, account: @account, hotel: @hotel, room: @room2, reported_by: @reporter_user)
    
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests",
        params: { priority: 'critical' },
        headers: @front_desk_headers
    
    assert_response :success
    data = json_response['data']
    
    assert_equal 1, data.size
    assert_equal 'critical', data.first['priority']
  end
  
  test "GET /api/v1/hotels/:hotel_id/maintenance_requests with category filter" do
    electrical_request = create(:maintenance_request, category: 'electrical', account: @account, hotel: @hotel, room: @room2, reported_by: @reporter_user)
    plumbing_request = create(:maintenance_request, category: 'plumbing', account: @account, hotel: @hotel, room: @room2, reported_by: @reporter_user)
    
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests",
        params: { category: 'electrical' },
        headers: @admin_headers
    
    assert_response :success
    data = json_response['data']
    
    assert_equal 2, data.size # @maintenance_request (default electrical) and electrical_request
    assert data.all? { |r| r['category'] == 'electrical' }
  end
  
  test "POST /api/v1/hotels/:hotel_id/maintenance_requests creates new request" do
    request_params = {
      room_id: @room.id,
      category: 'plumbing',
      issue_description: 'Leaking faucet in bathroom',
      priority: 'high',
      scheduled_date: Date.tomorrow.to_s,
      cost_estimate: '150.00'
    }
    
    assert_difference 'MaintenanceRequest.count', 1 do
      post "/api/v1/hotels/#{@hotel.id}/maintenance_requests",
           params: request_params.to_json,
           headers: @front_desk_headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal 'plumbing', data['category']
    assert_equal 'Leaking faucet in bathroom', data['issue_description']
    assert_equal 'high', data['priority']
    assert_equal 'open', data['status']
    assert_equal @room.id, data['room_id']
    assert_equal '150.0', data['cost_estimate']
    assert_equal @front_desk_agent.user.id, data['reported_by']['id']
  end
  
  test "POST /api/v1/hotels/:hotel_id/maintenance_requests for common area" do
    request_params = {
      category: 'hvac',
      issue_description: 'Lobby AC not working',
      priority: 'critical'
    }
    
    assert_difference 'MaintenanceRequest.count', 1 do
      post "/api/v1/hotels/#{@hotel.id}/maintenance_requests",
           params: request_params.to_json,
           headers: @admin_headers
    end
    
    assert_response :created
    
    data = json_response['data']
    assert_nil data['room_id']
    assert_equal 'Common Area', data['location_description']
  end
  
  test "POST /api/v1/hotels/:hotel_id/maintenance_requests validates required fields" do
    request_params = {
      room_id: @room.id,
      priority: 'normal'
      # Missing category and issue_description
    }
    
    post "/api/v1/hotels/#{@hotel.id}/maintenance_requests",
         params: request_params.to_json,
         headers: @front_desk_headers
    
    assert_response :unprocessable_content
    assert json_response['error'].present?
  end
  
  test "GET /api/v1/hotels/:hotel_id/maintenance_requests/:id shows request details" do
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
        headers: @housekeeping_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @maintenance_request.id, data['id']
    assert_equal @maintenance_request.category, data['category']
    assert_equal @maintenance_request.issue_description, data['issue_description']
    assert data['reported_by'].present?
    assert data['assigned_to'].present?
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id updates request" do
    update_params = {
      priority: 'critical',
      scheduled_date: Date.tomorrow.to_s,
      cost_estimate: '500.00'
    }
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
        params: update_params.to_json,
        headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'critical', data['priority']
    assert_equal Date.tomorrow.to_s, data['scheduled_date']
    assert_equal '500.0', data['cost_estimate']
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id requires front_desk or above" do
    update_params = {
      priority: 'critical'
    }
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
        params: update_params.to_json,
        headers: @housekeeping_headers
    
    assert_response :forbidden
  end
  
  test "DELETE /api/v1/hotels/:hotel_id/maintenance_requests/:id deletes request" do
    assert_difference 'MaintenanceRequest.count', -1 do
      delete "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
             headers: @admin_headers
    end

    assert_response :no_content
  end

  test "DELETE /api/v1/hotels/:hotel_id/maintenance_requests/:id requires manager or above" do
    delete "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
           headers: @front_desk_headers

    assert_response :forbidden
  end

  test "DELETE /api/v1/hotels/:hotel_id/maintenance_requests/:id returns 404 for non-existent request" do
    delete "/api/v1/hotels/#{@hotel.id}/maintenance_requests/999999",
           headers: @admin_headers

    assert_response :not_found
  end

  test "DELETE /api/v1/hotels/:hotel_id/maintenance_requests/:id returns 404 for request from different account" do
    other_room_type = create(:room_type, hotel: @other_hotel, account: @other_account)
    other_room = create(:room, hotel: @other_hotel, room_type: other_room_type, account: @other_account)
    other_request = create(:maintenance_request, account: @other_account, hotel: @other_hotel, room: other_room, reported_by: @reporter_user)

    delete "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{other_request.id}",
           headers: @admin_headers

    assert_response :not_found
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/assign assigns to user" do
    new_user = create(:user)
    new_account_user = AccountUser.create!(account: @account, user: new_user)
    new_account_user.add_role(:front_desk)
    
    assign_params = {
      assigned_to_id: new_user.id
    }
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/assign",
        params: assign_params.to_json,
        headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal new_user.id, data['assigned_to']['id']
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/assign can unassign" do
    assign_params = {
      assigned_to_id: nil
    }
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/assign",
        params: assign_params.to_json,
        headers: @admin_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_nil data['assigned_to']
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/start starts work" do
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/start",
        headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'in_progress', data['status']
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/start requires open status" do
    @maintenance_request.update!(status: 'completed')
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/start",
        headers: @front_desk_headers
    
    assert_response :conflict
    assert json_response['error'].present?
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/complete completes request" do
    @maintenance_request.update!(status: 'in_progress')
    
    complete_params = {
      resolution_notes: 'Fixed the electrical outlet',
      actual_cost: '125.50'
    }
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete",
        params: complete_params.to_json,
        headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'completed', data['status']
    assert_equal 'Fixed the electrical outlet', data['resolution_notes']
    assert_equal '125.5', data['actual_cost']
    assert data['completed_at'].present?
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/complete from pending_parts" do
    @maintenance_request.update!(status: 'pending_parts')
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete",
        headers: @admin_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'completed', data['status']
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/complete requires in_progress or pending_parts" do
    @maintenance_request.update!(status: 'open')
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/complete",
        headers: @front_desk_headers
    
    assert_response :conflict
    assert json_response['error'].present?
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/cancel cancels request" do
    cancel_params = {
      reason: 'No longer needed'
    }
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/cancel",
        params: cancel_params.to_json,
        headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'cancelled', data['status']
    assert_equal 'No longer needed', data['resolution_notes']
  end
  
  test "PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/cancel cannot cancel completed" do
    @maintenance_request.update!(status: 'completed')
    
    put "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}/cancel",
        headers: @admin_headers
    
    assert_response :conflict
    assert json_response['error'].present?
  end
  
  test "requests from other hotels are not accessible" do
    other_request = create(:maintenance_request, 
                          account: @other_account, 
                          hotel: @other_hotel,
                          reported_by: @reporter_user)
    
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{other_request.id}",
        headers: @admin_headers
    
    assert_response :not_found
  end
  
  test "pagination works correctly" do
    # Create 30 requests total (including @maintenance_request)
    29.times do
      create(:maintenance_request, 
             account: @account, 
             hotel: @hotel, 
             room: @room,
             reported_by: @reporter_user)
    end
    
    # First page - default per_page is 25, so ask for 10
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests?page=1&per_page=10",
        headers: @front_desk_headers
    
    assert_response :success
    
    data = json_response['data']
    meta = json_response['meta']
    links = json_response['links']
    
    assert_equal 10, data.size
    assert_equal 30, meta['total_count']
    assert_equal 3, meta['total_pages']
    assert_equal 1, meta['current_page']
    assert links['next'].present?
    assert_nil links['prev']
  end
  
  test "includes computed fields" do
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
        headers: @front_desk_headers
    
    assert_response :success
    
    data = json_response['data']
    assert data['location_description'].present?
    assert data['days_open'].present?
    assert_equal "Room #{@room.room_number}", data['location_description']
  end
  
  test "includes room and user associations" do
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
        headers: @admin_headers
    
    assert_response :success
    
    data = json_response['data']
    assert data['room'].present?
    assert_equal @room.id, data['room']['id']
    assert data['reported_by'].present?
    assert_equal @reporter_user.email, data['reported_by']['email']
    assert data['assigned_to'].present?
    assert_equal @maintenance_user.email, data['assigned_to']['email']
  end
  
  test "housekeeping can report maintenance issues" do
    request_params = {
      room_id: @room.id,
      category: 'furniture',
      issue_description: 'Broken bed frame',
      priority: 'high'
    }
    
    assert_difference 'MaintenanceRequest.count', 1 do
      post "/api/v1/hotels/#{@hotel.id}/maintenance_requests",
           params: request_params.to_json,
           headers: @housekeeping_headers
    end
    
    assert_response :created
  end
  
  test "cost variance fields are calculated correctly" do
    @maintenance_request.update!(cost_estimate: 100.0, actual_cost: 125.0)

    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests/#{@maintenance_request.id}",
        headers: @front_desk_headers

    assert_response :success

    data = json_response['data']
    assert_equal '25.0', data['cost_variance']
    assert_equal 25.0, data['cost_variance_percentage']
  end

  test "returns 401 without API key" do
    get "/api/v1/hotels/#{@hotel.id}/maintenance_requests"

    assert_response :unauthorized
  end
end