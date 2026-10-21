require "test_helper"

class Api::V1::ReservationLookupTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @guest = create(:guest, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    
    # Create a reservation with a known confirmation number
    @reservation = create(:reservation,
      hotel: @hotel,
      account: @account,
      guest: @guest,
      room_type: @room_type,
      confirmation_number: 'TEST123ABC',
      check_in_date: Date.tomorrow,
      check_out_date: Date.tomorrow + 2.days,
      status: 'confirmed'
    )
    
    # Create API agent with front_desk role
    @api_agent = create_api_agent(@account, :front_desk)
    @headers = api_headers_for(@api_agent)
  end
  
  test "GET /api/v1/reservations/lookup finds reservation by confirmation number" do
    get "/api/v1/reservations/lookup",
        params: { confirmation_number: 'TEST123ABC' },
        headers: @headers
    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal @reservation.id, data['id']
    assert_equal 'TEST123ABC', data['confirmation_number']
    assert_equal @guest.email, data['guest']['email']
  end
  
  test "GET /api/v1/reservations/lookup returns 404 for non-existent confirmation number" do
    get "/api/v1/reservations/lookup",
        params: { confirmation_number: 'NONEXISTENT' },
        headers: @headers
    
    assert_response :not_found
    assert_json_response
    
    assert_equal 'Not Found', json_response['error']
  end
  
  test "GET /api/v1/reservations/lookup returns 400 when no lookup parameter is provided" do
    get "/api/v1/reservations/lookup",
        headers: @headers

    assert_response :bad_request
    assert_json_response

    assert_equal 'Bad Request', json_response['error']
    assert_match /Either confirmation_number or email parameter is required/, json_response['message']
  end
  
  test "GET /api/v1/reservations/lookup returns 401 without API key" do
    get "/api/v1/reservations/lookup",
        params: { confirmation_number: 'TEST123ABC' }
    
    assert_response :unauthorized
  end
  
  test "GET /api/v1/reservations/lookup only finds reservations within current account" do
    # Create another account and hotel
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)
    
    # Create reservation in other account
    other_reservation = create(:reservation,
      hotel: other_hotel,
      account: other_account,
      guest: @guest,
      room_type: create(:room_type, hotel: other_hotel, account: other_account),
      confirmation_number: 'OTHER123',
      check_in_date: Date.tomorrow,
      check_out_date: Date.tomorrow + 2.days,
      status: 'confirmed'
    )
    
    # Try to lookup other account's reservation - should not find it
    get "/api/v1/reservations/lookup",
        params: { confirmation_number: 'OTHER123' },
        headers: @headers
    
    assert_response :not_found
    assert_json_response
    assert_equal 'Not Found', json_response['error']
  end
  
  test "GET /api/v1/reservations/lookup requires proper authorization" do
    # Create a housekeeping agent (should NOT have access to view reservations)
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    housekeeping_headers = api_headers_for(housekeeping_agent)

    get "/api/v1/reservations/lookup",
        params: { confirmation_number: 'TEST123ABC' },
        headers: housekeeping_headers

    assert_response :forbidden
    assert_json_response
    assert_equal 'Forbidden', json_response['error']
  end

  # Email lookup tests

  test "GET /api/v1/reservations/lookup finds reservations by email" do
    get "/api/v1/reservations/lookup",
        params: { email: @guest.email },
        headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_kind_of Array, data
    assert_equal 1, data.length
    assert_equal @reservation.id, data.first['id']
    assert_equal @guest.email, data.first['guest']['email']
  end

  test "GET /api/v1/reservations/lookup email search is case-insensitive" do
    get "/api/v1/reservations/lookup",
        params: { email: @guest.email.upcase },
        headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_kind_of Array, data
    assert_equal 1, data.length
    assert_equal @reservation.id, data.first['id']
  end

  test "GET /api/v1/reservations/lookup returns 404 for non-existent email" do
    get "/api/v1/reservations/lookup",
        params: { email: 'nonexistent@example.com' },
        headers: @headers

    assert_response :not_found
    assert_json_response
    assert_equal 'Not Found', json_response['error']
  end

  test "GET /api/v1/reservations/lookup email returns multiple reservations for same guest" do
    # Create a second reservation for the same guest
    second_reservation = create(:reservation,
      hotel: @hotel,
      account: @account,
      guest: @guest,
      room_type: @room_type,
      confirmation_number: 'TEST456DEF',
      check_in_date: Date.tomorrow + 10.days,
      check_out_date: Date.tomorrow + 12.days,
      status: 'confirmed'
    )

    get "/api/v1/reservations/lookup",
        params: { email: @guest.email },
        headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_kind_of Array, data
    assert_equal 2, data.length

    confirmation_numbers = data.map { |r| r['confirmation_number'] }
    assert_includes confirmation_numbers, 'TEST123ABC'
    assert_includes confirmation_numbers, 'TEST456DEF'
  end

  test "GET /api/v1/reservations/lookup email only finds reservations within current account" do
    # Create another account with a guest using the same email
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account)
    other_guest = create(:guest, account: other_account, email: @guest.email)

    other_reservation = create(:reservation,
      hotel: other_hotel,
      account: other_account,
      guest: other_guest,
      room_type: create(:room_type, hotel: other_hotel, account: other_account),
      confirmation_number: 'OTHER456',
      check_in_date: Date.tomorrow,
      check_out_date: Date.tomorrow + 2.days,
      status: 'confirmed'
    )

    # Should only find the reservation in our account
    get "/api/v1/reservations/lookup",
        params: { email: @guest.email },
        headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_equal 1, data.length
    assert_equal @reservation.id, data.first['id']
  end
end