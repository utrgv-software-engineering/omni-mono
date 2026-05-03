# frozen_string_literal: true

class MaintenanceRequestsController < StaffController
  before_action :set_hotel
  before_action :set_maintenance_request, only: [:show, :edit, :update, :destroy, :assign, :complete, :cancel]

  def index
    @maintenance_requests = policy_scope(@hotel.maintenance_requests)
                           .includes(:room, :reported_by, :assigned_to)
                           .order(created_at: :desc)
    authorize MaintenanceRequest
    
    # Apply filters if present
    if params[:status].present?
      @maintenance_requests = @maintenance_requests.where(status: params[:status])
    end
    
    if params[:priority].present?
      @maintenance_requests = @maintenance_requests.where(priority: params[:priority])
    end
    
    if params[:category].present?
      @maintenance_requests = @maintenance_requests.where(category: params[:category])
    end
    
    if params[:room_id].present?
      @maintenance_requests = @maintenance_requests.where(room_id: params[:room_id])
    end
  end

  def show
    authorize @maintenance_request
  end

  def new
    @maintenance_request = @hotel.maintenance_requests.build
    @maintenance_request.reported_by = current_user
    authorize @maintenance_request
    @rooms = @hotel.rooms.order(:room_number)
    @users = current_tenant.users.joins(:account_users)
                           .where(account_users: { account_id: current_tenant.id })
                           .distinct
  end

  def create
    @maintenance_request = @hotel.maintenance_requests.build(maintenance_request_params)
    @maintenance_request.reported_by = current_user unless @maintenance_request.reported_by
    authorize @maintenance_request

    if @maintenance_request.save
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  notice: 'Maintenance request was successfully created.'
    else
      @rooms = @hotel.rooms.order(:room_number)
      @users = current_tenant.users.joins(:account_users)
                             .where(account_users: { account_id: current_tenant.id })
                             .distinct
      render :new, status: :unprocessable_content
    end
  end

  def edit
    authorize @maintenance_request
    @rooms = @hotel.rooms.order(:room_number)
    @users = current_tenant.users.joins(:account_users)
                           .where(account_users: { account_id: current_tenant.id })
                           .distinct
  end

  def update
    authorize @maintenance_request
    
    if @maintenance_request.update(maintenance_request_params)
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  notice: 'Maintenance request was successfully updated.'
    else
      @rooms = @hotel.rooms.order(:room_number)
      @users = current_tenant.users.joins(:account_users)
                             .where(account_users: { account_id: current_tenant.id })
                             .distinct
      render :edit, status: :unprocessable_content
    end
  end

  def destroy
    authorize @maintenance_request
    @maintenance_request.destroy
    redirect_to hotel_maintenance_requests_path(@hotel), 
                notice: 'Maintenance request was successfully destroyed.'
  end

  # Custom actions
  def assign
    authorize @maintenance_request
    
    user_id = params[:assigned_to_id]
    user = user_id.present? ? User.find(user_id) : nil
    
    if @maintenance_request.update(assigned_to: user)
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  notice: 'Maintenance request successfully assigned.'
    else
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  alert: 'Unable to assign maintenance request.'
    end
  end

  def complete
    authorize @maintenance_request
    
    resolution_notes = params[:resolution_notes]
    actual_cost = params[:actual_cost]
    
    if @maintenance_request.complete!(resolution_notes: resolution_notes, actual_cost: actual_cost)
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  notice: 'Maintenance request marked as complete.'
    else
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  alert: 'Unable to complete maintenance request.'
    end
  end

  def cancel
    authorize @maintenance_request
    
    reason = params[:reason]
    
    if @maintenance_request.cancel!(reason)
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  notice: 'Maintenance request cancelled.'
    else
      redirect_to hotel_maintenance_request_path(@hotel, @maintenance_request), 
                  alert: 'Unable to cancel maintenance request.'
    end
  end

  private

  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end

  def set_maintenance_request
    @maintenance_request = @hotel.maintenance_requests.find(params[:id])
  end

  def maintenance_request_params
    params.require(:maintenance_request).permit(
      :room_id, :category, :issue_description, :priority, :status,
      :scheduled_date, :cost_estimate, :actual_cost, :assigned_to_id,
      :resolution_notes, :completed_at
    )
  end
end