# frozen_string_literal: true

class ServicesController < StaffController
  before_action :set_hotel
  before_action :set_service, only: [:show, :edit, :update, :destroy]

  def index
    @services = policy_scope(@hotel.services)
    authorize Service
  end

  def show
    authorize @service
  end

  def new
    @service = @hotel.services.build
    authorize @service
  end

  def create
    @service = @hotel.services.build(service_params)
    authorize @service

    if @service.save
      redirect_to hotel_service_path(@hotel, @service), notice: 'Service was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end

  def edit
    authorize @service
  end

  def update
    authorize @service
    if @service.update(service_params)
      redirect_to hotel_service_path(@hotel, @service), notice: 'Service was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end

  def destroy
    authorize @service
    @service.destroy
    redirect_to hotel_services_path(@hotel), notice: 'Service was successfully destroyed.'
  end

  private

  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end

  def set_service
    @service = @hotel.services.find(params[:id])
  end

  def service_params
    params.require(:service).permit(
      :name, :description, :category, :price, :pricing_unit,
      :duration_minutes, :capacity, :advance_booking_required,
      :is_active, :location, :provider_info
    )
  end
end