require "test_helper"

class Api::V1::CheckInOutTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, room_type: @room_type, account: @account, status: 'available')
    @room2 = create(:room, hotel: @hotel, room_type: @room_type, account: @account, status: 'available')
    @guest = create(:guest, account: @account)
    
    # Create a confirmed reservation
    @reservation = create(:reservation,
                         hotel: @hotel,
                         guest: @guest,
                         room_type: @room_type,
                         account: @account,
                         status: 'confirmed',
                         check_in_date: Date.current,
                         check_out_date: Date.current + 2.days,
                         total_amount: 299.99)
    
    @api_agent = create_api_agent(@account, :front_desk)
    @headers = api_headers_for(@api_agent)
  end
  
  # Check-in tests
  test "GET /api/v1/reservations/:id/check_in/available_rooms returns available rooms" do
    # Make one room occupied
    @room2.update!(status: 'occupied')
    
    get "/api/v1/reservations/#{@reservation.id}/check_in/available_rooms", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 1, data.size
    assert_equal @room.id, data[0]['id']
    assert_equal 'available', data[0]['status']
  end
  
  test "POST /api/v1/reservations/:id/check_in checks in guest with room assignment" do
    post "/api/v1/reservations/#{@reservation.id}/check_in",
         params: { room_id: @room.id }.to_json,
         headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'checked_in', data['status']
    assert_equal @room.id, data['room_id']
    assert_not_nil data['actual_check_in']
    
    # Verify room status changed
    @room.reload
    assert_equal 'occupied', @room.status
    
    # Verify reservation updated
    @reservation.reload
    assert_equal 'checked_in', @reservation.status
    assert_equal @room.id, @reservation.room_id
    assert_not_nil @reservation.actual_check_in
  end
  
  test "POST /api/v1/reservations/:id/check_in fails without room assignment" do
    post "/api/v1/reservations/#{@reservation.id}/check_in",
         params: {}.to_json,
         headers: @headers
    
    assert_response :bad_request
    assert_json_response
    assert json_response['message'].include?('Room assignment required')
  end
  
  test "POST /api/v1/reservations/:id/check_in fails for non-confirmed reservation" do
    @reservation.update!(status: 'cancelled')
    
    post "/api/v1/reservations/#{@reservation.id}/check_in",
         params: { room_id: @room.id }.to_json,
         headers: @headers
    
    assert_response :conflict
    assert_json_response
    assert json_response['message'].include?('Only confirmed reservations')
  end
  
  test "POST /api/v1/reservations/:id/check_in fails for unavailable room" do
    @room.update!(status: 'occupied')
    
    post "/api/v1/reservations/#{@reservation.id}/check_in",
         params: { room_id: @room.id }.to_json,
         headers: @headers
    
    assert_response :conflict
    assert_json_response
    assert json_response['message'].include?('not available')
  end
  
  test "POST /api/v1/reservations/:id/check_in fails for wrong room type" do
    other_room_type = create(:room_type, hotel: @hotel, account: @account)
    wrong_room = create(:room, hotel: @hotel, room_type: other_room_type, account: @account)
    
    post "/api/v1/reservations/#{@reservation.id}/check_in",
         params: { room_id: wrong_room.id }.to_json,
         headers: @headers
    
    assert_response :conflict
    assert_json_response
    assert json_response['message'].include?('Room type does not match')
  end
  
  test "POST /api/v1/reservations/:id/check_in fails for future check-in date" do
    future_reservation = create(:reservation,
                               hotel: @hotel,
                               guest: @guest,
                               room_type: @room_type,
                               account: @account,
                               status: 'confirmed',
                               check_in_date: Date.current + 7.days,
                               check_out_date: Date.current + 10.days)
    
    post "/api/v1/reservations/#{future_reservation.id}/check_in",
         params: { room_id: @room.id }.to_json,
         headers: @headers
    
    assert_response :conflict
    assert_json_response
    assert json_response['message'].include?('Check-in date is in the future')
  end
  
  # Check-out tests
  test "GET /api/v1/reservations/:id/check_out/summary returns checkout summary" do
    # Check in first
    @reservation.update!(status: 'checked_in', room: @room, actual_check_in: Time.current)
    @room.update!(status: 'occupied')
    
    get "/api/v1/reservations/#{@reservation.id}/check_out/summary", headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert data['reservation'].present?
    assert_equal @reservation.total_amount.to_s, data['total_amount'].to_s
    assert_equal "0.0", data['paid_amount'].to_s
    assert_equal @reservation.total_amount.to_s, data['balance_due'].to_s
  end
  
  test "POST /api/v1/reservations/:id/check_out checks out guest" do
    # Check in first
    @reservation.update!(status: 'checked_in', room: @room, actual_check_in: Time.current)
    @room.update!(status: 'occupied')
    
    post "/api/v1/reservations/#{@reservation.id}/check_out",
         params: { payment_amount: 299.99 }.to_json,
         headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'checked_out', data['status']
    assert_not_nil data['actual_check_out']
    assert_equal 'paid', data['payment_status']
    
    # Verify room status changed
    @room.reload
    assert_equal 'cleaning', @room.status
    
    # Verify reservation updated
    @reservation.reload
    assert_equal 'checked_out', @reservation.status
    assert_not_nil @reservation.actual_check_out
    assert_equal 299.99, @reservation.paid_amount
    assert_equal 'paid', @reservation.payment_status
  end
  
  test "POST /api/v1/reservations/:id/check_out with partial payment" do
    # Check in first
    @reservation.update!(status: 'checked_in', room: @room, actual_check_in: Time.current)
    @room.update!(status: 'occupied')
    
    post "/api/v1/reservations/#{@reservation.id}/check_out",
         params: { payment_amount: 150.00 }.to_json,
         headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 'checked_out', data['status']
    assert_equal 'partial', data['payment_status']
    assert_equal "150.0", data['paid_amount'].to_s
  end
  
  test "POST /api/v1/reservations/:id/check_out creates housekeeping task when requested" do
    # Check in first
    @reservation.update!(status: 'checked_in', room: @room, actual_check_in: Time.current)
    @room.update!(status: 'occupied')
    
    assert_difference('HousekeepingTask.count', 1) do
      post "/api/v1/reservations/#{@reservation.id}/check_out",
           params: { create_housekeeping_task: true }.to_json,
           headers: @headers
    end
    
    assert_response :success
    
    task = HousekeepingTask.last
    assert_equal @room.id, task.room_id
    assert_equal 'deep_clean', task.task_type
    assert_equal 'high', task.priority
  end
  
  test "POST /api/v1/reservations/:id/check_out fails for non-checked-in reservation" do
    post "/api/v1/reservations/#{@reservation.id}/check_out",
         params: {}.to_json,
         headers: @headers
    
    assert_response :conflict
    assert_json_response
    assert json_response['message'].include?('Only checked-in reservations')
  end
  
  test "POST /api/v1/reservations/:id/check_out rejects negative payment" do
    # Check in first
    @reservation.update!(status: 'checked_in', room: @room, actual_check_in: Time.current)
    @room.update!(status: 'occupied')
    
    post "/api/v1/reservations/#{@reservation.id}/check_out",
         params: { payment_amount: -50 }.to_json,
         headers: @headers
    
    assert_response :bad_request
    assert_json_response
    assert json_response['message'].include?('cannot be negative')
  end

  test "returns 401 without API key" do
    get "/api/v1/reservations/#{@reservation.id}/check_in/available_rooms"

    assert_response :unauthorized
  end

  test "housekeeping role cannot perform check-in" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)

    post "/api/v1/reservations/#{@reservation.id}/check_in",
         params: { room_id: @room.id }.to_json,
         headers: headers

    assert_response :forbidden
  end

  test "returns 404 for reservation from different account" do
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)
    other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
    other_guest = create(:guest, account: other_account)
    other_reservation = create(:reservation,
                               hotel: other_hotel,
                               guest: other_guest,
                               room_type: other_room_type,
                               account: other_account,
                               status: 'confirmed')

    get "/api/v1/reservations/#{other_reservation.id}/check_in/available_rooms",
        headers: @headers

    assert_response :not_found
  end
end