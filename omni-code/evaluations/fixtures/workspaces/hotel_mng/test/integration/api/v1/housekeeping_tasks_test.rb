require "test_helper"

class Api::V1::HousekeepingTasksTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @other_account = create(:account)
    @other_hotel = create(:hotel, account: @other_account)
    
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    @room2 = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    
    # Create a housekeeper user for assignment
    @housekeeper_user = create(:user)
    @housekeeper_account_user = AccountUser.create!(account: @account, user: @housekeeper_user)
    @housekeeper_account_user.add_role(:housekeeping)
    
    # Create default housekeeping task
    @task = create(:housekeeping_task, 
                   account: @account, 
                   hotel: @hotel, 
                   room: @room,
                   assigned_to: @housekeeper_user)
    
    # Create API agents with different roles
    @admin_agent = create_api_agent(@account, :admin)
    @admin_headers = api_headers_for(@admin_agent)
    
    @front_desk_agent = create_api_agent(@account, :front_desk)
    @front_desk_headers = api_headers_for(@front_desk_agent)
    
    @housekeeping_agent = create_api_agent(@account, :housekeeping)
    @housekeeping_headers = api_headers_for(@housekeeping_agent)
  end
  
  test "GET /api/v1/hotels/:hotel_id/housekeeping_tasks returns list of tasks" do
    # Create additional tasks
    task2 = create(:housekeeping_task, :urgent, account: @account, hotel: @hotel, room: @room2)
    task3 = create(:housekeeping_task, :completed, account: @account, hotel: @hotel, room: @room)
    
    # Task from different hotel shouldn't appear
    other_task = create(:housekeeping_task, account: @other_account, hotel: @other_hotel)
    
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 3, data.size
    
    task_ids = data.map { |t| t['id'] }
    assert_includes task_ids, @task.id
    assert_includes task_ids, task2.id
    assert_includes task_ids, task3.id
    assert_not_includes task_ids, other_task.id
  end
  
  test "GET /api/v1/hotels/:hotel_id/housekeeping_tasks with status filter" do
    pending_task = create(:housekeeping_task, status: 'pending', account: @account, hotel: @hotel, room: @room2)
    completed_task = create(:housekeeping_task, :completed, account: @account, hotel: @hotel, room: @room2)
    
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", 
        params: { status: 'pending' }, 
        headers: @housekeeping_headers
    
    assert_response :success
    data = json_response['data']
    
    assert_equal 2, data.size # @task (default pending) and pending_task
    assert data.all? { |t| t['status'] == 'pending' }
  end
  
  test "GET /api/v1/hotels/:hotel_id/housekeeping_tasks with date filter" do
    today_task = create(:housekeeping_task, scheduled_date: Date.current, account: @account, hotel: @hotel, room: @room2)
    tomorrow_task = create(:housekeeping_task, scheduled_date: Date.tomorrow, account: @account, hotel: @hotel, room: @room2)
    
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", 
        params: { date: Date.current.to_s }, 
        headers: @housekeeping_headers
    
    assert_response :success
    data = json_response['data']
    
    assert_equal 2, data.size # @task (default today) and today_task
    assert data.all? { |t| t['scheduled_date'] == Date.current.to_s }
  end
  
  test "GET /api/v1/hotels/:hotel_id/housekeeping_tasks with room filter" do
    room2_task = create(:housekeeping_task, account: @account, hotel: @hotel, room: @room2)
    
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", 
        params: { room_id: @room.id }, 
        headers: @housekeeping_headers
    
    assert_response :success
    data = json_response['data']
    
    assert_equal 1, data.size
    assert_equal @room.id, data[0]['room_id']
  end
  
  test "GET /api/v1/hotels/:hotel_id/housekeeping_tasks with priority sort" do
    urgent_task = create(:housekeeping_task, :urgent, account: @account, hotel: @hotel, room: @room2)
    high_task = create(:housekeeping_task, :high_priority, account: @account, hotel: @hotel, room: @room2)
    low_task = create(:housekeeping_task, :low_priority, account: @account, hotel: @hotel, room: @room2)
    
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", 
        params: { sort: 'priority' }, 
        headers: @housekeeping_headers
    
    assert_response :success
    data = json_response['data']
    
    # Verify urgent tasks come first
    priorities = data.map { |t| t['priority'] }
    assert_equal 'urgent', priorities.first
  end
  
  test "GET /api/v1/hotels/:hotel_id/housekeeping_tasks/:id shows task details" do
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}", headers: @housekeeping_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @task.id, data['id']
    assert_equal @task.task_type, data['task_type']
    assert_equal @task.priority, data['priority']
    assert_equal @task.status, data['status']
    assert_equal @task.room_id, data['room_id']
    assert data['room'].present?
    assert data['assigned_to'].present?
  end
  
  test "GET /api/v1/hotels/:hotel_id/housekeeping_tasks/:id returns 404 for task from different hotel" do
    other_task = create(:housekeeping_task, account: @other_account, hotel: @other_hotel)
    
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{other_task.id}", headers: @front_desk_headers
    
    assert_response :not_found
  end
  
  test "POST /api/v1/hotels/:hotel_id/housekeeping_tasks creates new task" do
    task_params = {
      room_id: @room2.id,
      task_type: 'cleaning',
      priority: 'high',
      scheduled_date: Date.tomorrow.to_s,
      scheduled_time: '10:00',
      notes: 'Guest checkout cleaning'
    }
    
    assert_difference('HousekeepingTask.count', 1) do
      post "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", 
           params: task_params.to_json,
           headers: @front_desk_headers
    end
    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert_equal @room2.id, data['room_id']
    assert_equal 'cleaning', data['task_type']
    assert_equal 'high', data['priority']
    assert_equal 'pending', data['status']
    assert_equal Date.tomorrow.to_s, data['scheduled_date']
  end
  
  test "POST /api/v1/hotels/:hotel_id/housekeeping_tasks validates required fields" do
    invalid_params = {
      task_type: 'invalid_type',
      priority: 'invalid_priority'
    }
    
    post "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", 
         params: invalid_params.to_json,
         headers: @front_desk_headers
    
    assert_response :unprocessable_content
    assert_json_response
    assert json_response['errors'].present?
  end
  
  test "PATCH /api/v1/hotels/:hotel_id/housekeeping_tasks/:id updates task" do
    patch "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}", 
          params: { 
            priority: 'urgent',
            notes: 'Updated notes'
          }.to_json,
          headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'urgent', data['priority']
    assert_equal 'Updated notes', data['notes']
    
    @task.reload
    assert_equal 'urgent', @task.priority
    assert_equal 'Updated notes', @task.notes
  end
  
  test "housekeeping role can only update status fields" do
    patch "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}", 
          params: { 
            status: 'in_progress',
            notes: 'Started cleaning',
            priority: 'urgent',  # Should be ignored
            task_type: 'inspection'  # Should be ignored
          }.to_json,
          headers: @housekeeping_headers
    
    assert_response :success
    assert_json_response
    
    @task.reload
    assert_equal 'in_progress', @task.status
    assert_equal 'Started cleaning', @task.notes
    assert_equal 'normal', @task.priority  # Should not change
    assert_equal 'cleaning', @task.task_type  # Should not change
  end
  
  test "DELETE /api/v1/hotels/:hotel_id/housekeeping_tasks/:id deletes task" do
    assert_difference('HousekeepingTask.count', -1) do
      delete "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}",
             headers: @admin_headers
    end

    assert_response :no_content
  end

  test "DELETE /api/v1/hotels/:hotel_id/housekeeping_tasks/:id returns 404 for non-existent task" do
    delete "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/999999",
           headers: @admin_headers

    assert_response :not_found
  end

  test "DELETE /api/v1/hotels/:hotel_id/housekeeping_tasks/:id returns 404 for task from different account" do
    other_room_type = create(:room_type, hotel: @other_hotel, account: @other_account)
    other_room = create(:room, hotel: @other_hotel, room_type: other_room_type, account: @other_account)
    other_task = create(:housekeeping_task, account: @other_account, hotel: @other_hotel, room: other_room)

    delete "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{other_task.id}",
           headers: @admin_headers

    assert_response :not_found
  end

  test "front_desk role cannot delete tasks" do
    delete "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}",
           headers: @front_desk_headers

    assert_response :forbidden
  end

  test "housekeeping role cannot delete tasks" do
    delete "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}",
           headers: @housekeeping_headers

    assert_response :forbidden
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/assign assigns task to user" do
    new_housekeeper = create(:user)
    new_account_user = AccountUser.create!(account: @account, user: new_housekeeper)
    
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign",
        params: { assigned_to_id: new_housekeeper.id }.to_json,
        headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal new_housekeeper.id, data['assigned_to']['id']
    
    @task.reload
    assert_equal new_housekeeper.id, @task.assigned_to_id
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/assign requires assigned_to_id" do
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign",
        params: {}.to_json,
        headers: @front_desk_headers
    
    assert_response :bad_request
    assert_json_response
    assert json_response['message'].include?('assigned_to_id is required')
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/assign returns 404 for invalid user" do
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign",
        params: { assigned_to_id: 999999 }.to_json,
        headers: @front_desk_headers
    
    assert_response :not_found
  end
  
  test "housekeeping role cannot assign tasks" do
    new_user = create(:user)
    AccountUser.create!(account: @account, user: new_user)
    
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign",
        params: { assigned_to_id: new_user.id }.to_json,
        headers: @housekeeping_headers
    
    assert_response :forbidden
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/start starts task" do
    pending_task = create(:housekeeping_task, status: 'pending', account: @account, hotel: @hotel, room: @room2)
    
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{pending_task.id}/start",
        headers: @housekeeping_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'in_progress', data['status']
    assert data['started_at'].present?
    
    pending_task.reload
    assert_equal 'in_progress', pending_task.status
    assert pending_task.started_at.present?
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/start fails if already completed" do
    completed_task = create(:housekeeping_task, :completed, account: @account, hotel: @hotel, room: @room2)
    
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{completed_task.id}/start",
        headers: @housekeeping_headers
    
    assert_response :conflict
    assert_json_response
    assert json_response['message'].include?('pending or cancelled')
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/complete completes task" do
    in_progress_task = create(:housekeeping_task, :in_progress, account: @account, hotel: @hotel, room: @room2)
    
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{in_progress_task.id}/complete",
        params: { inspection_score: 8 }.to_json,
        headers: @housekeeping_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'completed', data['status']
    assert_equal 8, data['inspection_score']
    assert data['completed_at'].present?
    
    in_progress_task.reload
    assert_equal 'completed', in_progress_task.status
    assert_equal 8, in_progress_task.inspection_score
    assert in_progress_task.completed_at.present?
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/complete fails if not in progress" do
    pending_task = create(:housekeeping_task, status: 'pending', account: @account, hotel: @hotel, room: @room2)
    
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{pending_task.id}/complete",
        headers: @housekeeping_headers
    
    assert_response :conflict
    assert_json_response
    assert json_response['message'].include?('must be in progress')
  end
  
  test "PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/complete validates inspection score range" do
    in_progress_task = create(:housekeeping_task, :in_progress, account: @account, hotel: @hotel, room: @room2)
    
    put "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks/#{in_progress_task.id}/complete",
        params: { inspection_score: 15 }.to_json,
        headers: @housekeeping_headers
    
    assert_response :bad_request
    assert_json_response
    assert json_response['message'].include?('between 1 and 10')
  end
  
  test "pagination works correctly" do
    # Create 30 more tasks
    30.times do
      create(:housekeeping_task, account: @account, hotel: @hotel, room: @room)
    end
    
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks",
        params: { page: 1, per_page: 10 },
        headers: @front_desk_headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    meta = json_response['meta']
    
    assert_equal 10, data.size
    assert_equal 1, meta['current_page']
    assert meta['total_count'] > 30
    assert meta['total_pages'] >= 3
  end
  
  test "all staff roles can view housekeeping tasks" do
    roles = [:owner, :admin, :hotel_manager, :front_desk, :housekeeping]
    
    roles.each do |role|
      agent = create_api_agent(@account, role)
      headers = api_headers_for(agent)
      
      get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks", headers: headers
      assert_response :success, "#{role} should be able to view tasks"
    end
  end
  
  test "returns 401 without valid API key" do
    get "/api/v1/hotels/#{@hotel.id}/housekeeping_tasks",
        headers: { 'X-API-Key' => 'invalid' }
    
    assert_response :unauthorized
  end
  
  test "returns 404 for non-existent hotel" do
    get "/api/v1/hotels/999999/housekeeping_tasks",
        headers: @front_desk_headers
    
    assert_response :not_found
  end
end