# frozen_string_literal: true

class ServiceSchedulesController < StaffController
  before_action :set_hotel
  before_action :set_service
  before_action :set_service_schedule, only: [:show, :edit, :update, :destroy]

  def index
    @service_schedules = policy_scope(@service.service_schedules).ordered
    authorize ServiceSchedule
  end

  def show
    authorize @service_schedule
  end

  def new
    @service_schedule = @service.service_schedules.build
    authorize @service_schedule
  end

  def create
    @service_schedule = @service.service_schedules.build(service_schedule_params)
    authorize @service_schedule

    if @service_schedule.save
      redirect_to hotel_service_service_schedule_path(@hotel, @service, @service_schedule),
                  notice: 'Service schedule was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end

  def edit
    authorize @service_schedule
  end

  def update
    authorize @service_schedule
    if @service_schedule.update(service_schedule_params)
      redirect_to hotel_service_service_schedule_path(@hotel, @service, @service_schedule),
                  notice: 'Service schedule was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end

  def destroy
    authorize @service_schedule
    @service_schedule.destroy
    redirect_to hotel_service_service_schedules_path(@hotel, @service),
                notice: 'Service schedule was successfully destroyed.'
  end

  private

  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end

  def set_service
    @service = @hotel.services.find(params[:service_id])
  end

  def set_service_schedule
    @service_schedule = @service.service_schedules.find(params[:id])
  end

  def service_schedule_params
    params.require(:service_schedule).permit(
      :day_of_week, :start_time, :end_time, :max_bookings_per_slot
    )
  end
end