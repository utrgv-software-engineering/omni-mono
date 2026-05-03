require "test_helper"

class Api::V1::ReservationsTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 100, standard_occupancy: 2, extra_person_charge: 0)
    @room = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    @guest = create(:guest, account: @account)
    
    @api_agent = create_api_agent(@account, :front_desk)
    @headers = api_headers_for(@api_agent)
  end
  
  test "POST /api/v1/hotels/:hotel_id/reservations creates reservation with new guest" do
    reservation_params = {
      room_type_id: @room_type.id,
      check_in_date: Date.tomorrow.to_s,
      check_out_date: (Date.tomorrow + 2.days).to_s,
      adults: 2,
      children: 1,
      special_requests: "Late check-in",
      guest: {
        first_name: "John",
        last_name: "Doe",
        email: "john.doe@example.com",
        phone: "123-456-7890"
      }
    }
    
    assert_difference 'Reservation.count', 1 do
      assert_difference 'Guest.count', 1 do
        post "/api/v1/hotels/#{@hotel.id}/reservations",
             params: reservation_params.to_json,
             headers: @headers
      end
    end
    
    assert_api_conform(status: 201)

    
    assert_response :created
    assert_json_response
    
    data = json_response['data']
    assert data['confirmation_number'].present?
    assert_equal 'confirmed', data['status']
    assert_equal 2, data['adults']
    assert_equal 1, data['children']
    assert_equal '200.0', data['total_amount'] # 2 nights * $100
    assert_equal 'John', data['guest']['first_name']
  end
  
  test "POST /api/v1/hotels/:hotel_id/reservations creates reservation with existing guest" do
    reservation_params = {
      room_type_id: @room_type.id,
      check_in_date: Date.tomorrow.to_s,
      check_out_date: (Date.tomorrow + 1.day).to_s,
      adults: 1,
      guest: {
        id: @guest.id
      }
    }
    
    assert_difference 'Reservation.count', 1 do
      assert_no_difference 'Guest.count' do
        post "/api/v1/hotels/#{@hotel.id}/reservations",
             params: reservation_params.to_json,
             headers: @headers
      end
    end
    
    assert_api_conform(status: 201)

    
    assert_response :created
    data = json_response['data']
    assert_equal @guest.id, data['guest']['id']
  end
  
  test "POST /api/v1/hotels/:hotel_id/reservations validates dates" do
    reservation_params = {
      room_type_id: @room_type.id,
      check_in_date: Date.tomorrow.to_s,
      check_out_date: Date.yesterday.to_s,
      adults: 1,
      guest: {
        first_name: "Test",
        last_name: "User",
        email: "test@example.com"
      }
    }
    
    post "/api/v1/hotels/#{@hotel.id}/reservations",
         params: reservation_params.to_json,
         headers: @headers
    
    # Debug output to see what's happening
    if response.status == 404
      puts "Got 404 response. Body: #{response.body}"
      puts "Hotel ID: #{@hotel.id}"
      puts "Room Type ID: #{@room_type.id}"
    end
    
    assert_api_conform(status: 422)

    
    assert_response :unprocessable_content
    assert_json_response
    assert json_response['errors'].present?
  end
  
  test "GET /api/v1/hotels/:hotel_id/reservations returns list of reservations" do
    reservations = create_list(:reservation, 3, hotel: @hotel, guest: @guest, account: @account, room_type: @room_type)
    
    get "/api/v1/hotels/#{@hotel.id}/reservations", headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 3, data.length
  end
  
  test "GET /api/v1/hotels/:hotel_id/reservations filters by status" do
    create(:reservation, hotel: @hotel, guest: @guest, account: @account, room_type: @room_type, status: 'confirmed')
    create(:reservation, hotel: @hotel, guest: @guest, account: @account, room_type: @room_type, status: 'checked_in')
    
    get "/api/v1/hotels/#{@hotel.id}/reservations",
        params: { status: 'checked_in' },
        headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    data = json_response['data']
    assert_equal 1, data.length
    assert_equal 'checked_in', data.first['status']
  end
  
  test "GET /api/v1/reservations/:id returns reservation details" do
    reservation = create(:reservation, hotel: @hotel, guest: @guest, account: @account, room_type: @room_type)
    
    get "/api/v1/reservations/#{reservation.id}", headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal reservation.id, data['id']
    assert_equal reservation.confirmation_number, data['confirmation_number']
  end
  
  test "PATCH /api/v1/reservations/:id updates reservation" do
    reservation = create(:reservation, 
      hotel: @hotel, 
      guest: @guest,
      account: @account,
      room_type: @room_type,
      status: 'pending',
      adults: 1
    )
    
    update_params = {
      adults: 2,
      special_requests: "Need extra bed"
    }
    
    patch "/api/v1/reservations/#{reservation.id}",
          params: update_params.to_json,
          headers: @headers
    
    assert_api_conform(status: 200)

    
    assert_response :success
    assert_json_response
    
    data = json_response['data']
    assert_equal 2, data['adults']
    assert_equal "Need extra bed", data['special_requests']
  end
  
  test "PATCH /api/v1/reservations/:id only allows certain status changes" do
    reservation = create(:reservation, hotel: @hotel, guest: @guest, account: @account, room_type: @room_type, status: 'confirmed')
    
    patch "/api/v1/reservations/#{reservation.id}",
          params: { status: 'checked_in' }.to_json,
          headers: @headers
    
    assert_api_conform(status: 400)

    
    assert_response :bad_request
    assert_json_response
    assert_match /Invalid status/, json_response['message']
  end
  
  test "housekeeping role cannot create reservations" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)
    
    reservation_params = {
      room_type_id: @room_type.id,
      check_in_date: Date.tomorrow.to_s,
      check_out_date: (Date.tomorrow + 1.day).to_s,
      adults: 1,
      guest: { first_name: "Test", last_name: "User", email: "test@example.com" }
    }
    
    post "/api/v1/hotels/#{@hotel.id}/reservations",
         params: reservation_params.to_json,
         headers: headers
    
    assert_response :forbidden
  end
  
  test "cannot access reservations from other accounts" do
    other_hotel = create(:hotel) # Different account
    other_guest = create(:guest)
    other_room_type = create(:room_type, hotel: other_hotel, account: other_hotel.account)
    other_reservation = create(:reservation, hotel: other_hotel, account: other_hotel.account, guest: other_guest, room_type: other_room_type)

    get "/api/v1/reservations/#{other_reservation.id}", headers: @headers

    assert_api_conform(status: 404)


    assert_response :not_found
  end

  # DELETE tests (requires manager_or_above role)
  test "DELETE /api/v1/reservations/:id deletes cancelled reservation" do
    manager_agent = create_api_agent(@account, :hotel_manager)
    manager_headers = api_headers_for(manager_agent)

    reservation = create(:reservation,
      hotel: @hotel,
      guest: @guest,
      account: @account,
      room_type: @room_type,
      status: 'cancelled'
    )

    assert_difference('Reservation.count', -1) do
      delete "/api/v1/reservations/#{reservation.id}", headers: manager_headers
    end

    assert_response :no_content
  end

  test "DELETE /api/v1/reservations/:id deletes no_show reservation" do
    manager_agent = create_api_agent(@account, :hotel_manager)
    manager_headers = api_headers_for(manager_agent)

    reservation = create(:reservation,
      hotel: @hotel,
      guest: @guest,
      account: @account,
      room_type: @room_type,
      status: 'no_show'
    )

    assert_difference('Reservation.count', -1) do
      delete "/api/v1/reservations/#{reservation.id}", headers: manager_headers
    end

    assert_response :no_content
  end

  test "DELETE /api/v1/reservations/:id returns conflict for confirmed reservation" do
    manager_agent = create_api_agent(@account, :hotel_manager)
    manager_headers = api_headers_for(manager_agent)

    reservation = create(:reservation,
      hotel: @hotel,
      guest: @guest,
      account: @account,
      room_type: @room_type,
      status: 'confirmed'
    )

    assert_no_difference('Reservation.count') do
      delete "/api/v1/reservations/#{reservation.id}", headers: manager_headers
    end

    assert_response :conflict
    assert_json_response
    assert_equal 'Conflict', json_response['error']
  end

  test "DELETE /api/v1/reservations/:id returns conflict for checked_in reservation" do
    manager_agent = create_api_agent(@account, :hotel_manager)
    manager_headers = api_headers_for(manager_agent)

    reservation = create(:reservation,
      hotel: @hotel,
      guest: @guest,
      account: @account,
      room_type: @room_type,
      room: @room,
      status: 'checked_in'
    )

    assert_no_difference('Reservation.count') do
      delete "/api/v1/reservations/#{reservation.id}", headers: manager_headers
    end

    assert_response :conflict
    assert_json_response
    assert_equal 'Conflict', json_response['error']
  end

  test "DELETE /api/v1/reservations/:id returns 404 for non-existent reservation" do
    manager_agent = create_api_agent(@account, :hotel_manager)
    manager_headers = api_headers_for(manager_agent)

    delete "/api/v1/reservations/999999", headers: manager_headers

    assert_response :not_found
  end

  test "DELETE /api/v1/reservations/:id returns 403 for front_desk role" do
    reservation = create(:reservation,
      hotel: @hotel,
      guest: @guest,
      account: @account,
      room_type: @room_type,
      status: 'cancelled'
    )

    # @headers uses front_desk role from setup
    delete "/api/v1/reservations/#{reservation.id}", headers: @headers

    assert_response :forbidden
  end

  test "returns 401 without API key" do
    get "/api/v1/hotels/#{@hotel.id}/reservations"

    assert_response :unauthorized
  end
end