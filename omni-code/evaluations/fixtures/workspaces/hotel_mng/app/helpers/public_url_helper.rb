module PublicUrlHelper
  def hotel_public_path(path = '')
    if using_subdomain?
      "/public#{path}"
    else
      "/hotels/#{@hotel.subdomain}#{path}"
    end
  end

  def public_hotel_path
    hotel_public_path('/hotel')
  end

  def public_rooms_path(params = {})
    path = hotel_public_path('/rooms')
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def public_room_path(room_or_id, params = {})
    id = room_or_id.respond_to?(:id) ? room_or_id.id : room_or_id
    path = hotel_public_path("/rooms/#{id}")
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def public_availability_index_path(params = {})
    path = hotel_public_path('/availability')
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def public_bookings_path
    hotel_public_path('/bookings')
  end

  def new_public_booking_path(params = {})
    path = hotel_public_path('/bookings/new')
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def public_booking_path(booking_or_id)
    id = booking_or_id.respond_to?(:id) ? booking_or_id.id : booking_or_id
    hotel_public_path("/bookings/#{id}")
  end

  def public_reservation_lookup_path
    hotel_public_path('/reservations/lookup')
  end

  def public_reservation_search_path
    hotel_public_path('/reservations/search')
  end

  def public_reservation_path(reservation_or_id, params = {})
    id = reservation_or_id.respond_to?(:id) ? reservation_or_id.id : reservation_or_id
    path = hotel_public_path("/reservations/#{id}")
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def edit_public_reservation_path(reservation_or_id, params = {})
    id = reservation_or_id.respond_to?(:id) ? reservation_or_id.id : reservation_or_id
    path = hotel_public_path("/reservations/#{id}/edit")
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def cancel_public_reservation_path(reservation_or_id, params = {})
    id = reservation_or_id.respond_to?(:id) ? reservation_or_id.id : reservation_or_id
    path = hotel_public_path("/reservations/#{id}/cancel")
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def confirm_cancel_public_reservation_path(reservation_or_id, params = {})
    id = reservation_or_id.respond_to?(:id) ? reservation_or_id.id : reservation_or_id
    path = hotel_public_path("/reservations/#{id}/confirm_cancel")
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  def cancelled_public_reservation_path(reservation_or_id, params = {})
    id = reservation_or_id.respond_to?(:id) ? reservation_or_id.id : reservation_or_id
    path = hotel_public_path("/reservations/#{id}/cancelled")
    params.any? ? "#{path}?#{params.to_query}" : path
  end

  private

  def using_subdomain?
    request.subdomain.present? && request.subdomain != 'www' && request.subdomain == @hotel&.subdomain
  end
end