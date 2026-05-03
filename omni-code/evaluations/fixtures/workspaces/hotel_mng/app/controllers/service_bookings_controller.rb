# frozen_string_literal: true

class ServiceBookingsController < StaffController
  before_action :set_reservation, if: -> { params[:reservation_id].present? }
  before_action :set_service_booking, only: [:show, :edit, :update, :destroy]

  def index
    if @reservation
      @service_bookings = policy_scope(@reservation.service_bookings)
    else
      @service_bookings = policy_scope(ServiceBooking)
    end
    authorize ServiceBooking
  end

  def show
    authorize @service_booking
  end

  def new
    if @reservation
      @service_booking = @reservation.service_bookings.build
    else
      @service_booking = ServiceBooking.new
    end
    authorize @service_booking
    load_services
  end

  def create
    if @reservation
      @service_booking = @reservation.service_bookings.build(service_booking_params)
    else
      @service_booking = ServiceBooking.new(service_booking_params)
    end
    authorize @service_booking

    if @service_booking.save
      redirect_to service_booking_redirect_path(@service_booking), 
                  notice: 'Service booking was successfully created.'
    else
      load_services
      render :new, status: :unprocessable_content
    end
  end

  def edit
    authorize @service_booking
    load_services
  end

  def update
    authorize @service_booking
    if @service_booking.update(service_booking_params)
      redirect_to service_booking_redirect_path(@service_booking), 
                  notice: 'Service booking was successfully updated.'
    else
      load_services
      render :edit, status: :unprocessable_content
    end
  end

  def destroy
    authorize @service_booking
    @service_booking.destroy
    redirect_to service_bookings_redirect_path, notice: 'Service booking was successfully destroyed.'
  end

  private

  def set_reservation
    @reservation = Reservation.find(params[:reservation_id])
  end

  def set_service_booking
    @service_booking = if @reservation
                        @reservation.service_bookings.find(params[:id])
                      else
                        ServiceBooking.find(params[:id])
                      end
  end

  def service_booking_params
    params.require(:service_booking).permit(
      :service_id, :guest_id, :reservation_id, :booking_date, :booking_time,
      :quantity, :unit_price, :total_price, :duration_minutes, :status,
      :special_requests, :notes
    )
  end

  def load_services
    @services = Service.active.order(:name)
    @guests = Guest.order(:last_name, :first_name)
  end

  def service_booking_redirect_path(service_booking)
    if @reservation
      reservation_service_booking_path(@reservation, service_booking)
    else
      service_booking_path(service_booking)
    end
  end

  def service_bookings_redirect_path
    if @reservation
      reservation_service_bookings_path(@reservation)
    else
      service_bookings_path
    end
  end
end