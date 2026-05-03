# frozen_string_literal: true

class HousekeepingTasksController < StaffController
  before_action :set_hotel
  before_action :set_housekeeping_task, only: [:show, :edit, :update, :destroy, :mark_complete, :assign]

  def index
    @housekeeping_tasks = policy_scope(@hotel.housekeeping_tasks)
                          .includes(:room, :assigned_to)
                          .order(scheduled_date: :desc, scheduled_time: :asc)
    authorize HousekeepingTask
    
    # Apply filters if present
    if params[:status].present?
      @housekeeping_tasks = @housekeeping_tasks.where(status: params[:status])
    end
    
    if params[:date].present?
      @housekeeping_tasks = @housekeeping_tasks.for_date(params[:date])
    end
    
    if params[:room_id].present?
      @housekeeping_tasks = @housekeeping_tasks.where(room_id: params[:room_id])
    end
  end

  def show
    authorize @housekeeping_task
    @users = current_tenant.users.joins(:account_users)
                           .where(account_users: { account_id: current_tenant.id })
                           .distinct
  end

  def new
    @housekeeping_task = @hotel.housekeeping_tasks.build
    authorize @housekeeping_task
    @rooms = @hotel.rooms.order(:room_number)
    @users = current_tenant.users.joins(:account_users)
                           .where(account_users: { account_id: current_tenant.id })
                           .distinct
  end

  def create
    @housekeeping_task = @hotel.housekeeping_tasks.build(housekeeping_task_params)
    authorize @housekeeping_task

    if @housekeeping_task.save
      redirect_to hotel_housekeeping_task_path(@hotel, @housekeeping_task), 
                  notice: 'Housekeeping task was successfully created.'
    else
      @rooms = @hotel.rooms.order(:room_number)
      @users = current_tenant.users.joins(:account_users)
                             .where(account_users: { account_id: current_tenant.id })
                             .distinct
      render :new, status: :unprocessable_content
    end
  end

  def edit
    authorize @housekeeping_task
    @rooms = @hotel.rooms.order(:room_number)
    @users = current_tenant.users.joins(:account_users)
                           .where(account_users: { account_id: current_tenant.id })
                           .distinct
  end

  def update
    authorize @housekeeping_task
    
    if @housekeeping_task.update(housekeeping_task_params)
      redirect_to hotel_housekeeping_task_path(@hotel, @housekeeping_task), 
                  notice: 'Housekeeping task was successfully updated.'
    else
      @rooms = @hotel.rooms.order(:room_number)
      @users = current_tenant.users.joins(:account_users)
                             .where(account_users: { account_id: current_tenant.id })
                             .distinct
      render :edit, status: :unprocessable_content
    end
  end

  def destroy
    authorize @housekeeping_task
    @housekeeping_task.destroy
    redirect_to hotel_housekeeping_tasks_path(@hotel), 
                notice: 'Housekeeping task was successfully destroyed.'
  end

  # Custom actions
  def mark_complete
    authorize @housekeeping_task
    
    score = params[:inspection_score]
    
    if @housekeeping_task.complete!(score)
      redirect_to hotel_housekeeping_task_path(@hotel, @housekeeping_task), 
                  notice: 'Task marked as complete.'
    else
      redirect_to hotel_housekeeping_task_path(@hotel, @housekeeping_task), 
                  alert: 'Unable to mark task as complete.'
    end
  end

  def assign
    authorize @housekeeping_task
    
    user_id = params[:assigned_to_id]
    user = user_id.present? ? User.find(user_id) : nil
    
    if @housekeeping_task.update(assigned_to: user)
      redirect_to hotel_housekeeping_task_path(@hotel, @housekeeping_task), 
                  notice: 'Task successfully assigned.'
    else
      redirect_to hotel_housekeeping_task_path(@hotel, @housekeeping_task), 
                  alert: 'Unable to assign task.'
    end
  end

  private

  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end

  def set_housekeeping_task
    @housekeeping_task = @hotel.housekeeping_tasks.find(params[:id])
  end

  def housekeeping_task_params
    params.require(:housekeeping_task).permit(
      :room_id, :task_type, :priority, :status, :description, 
      :scheduled_date, :scheduled_time, :assigned_to_id,
      :notes, :inspection_score, :started_at, :completed_at
    )
  end
end