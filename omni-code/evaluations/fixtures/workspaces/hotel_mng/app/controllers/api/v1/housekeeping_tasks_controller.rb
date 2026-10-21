class Api::V1::HousekeepingTasksController < Api::V1::BaseController
  before_action :set_hotel
  before_action :set_housekeeping_task, only: [:show, :update, :destroy, :assign, :start, :complete]
  
  # GET /api/v1/hotels/:hotel_id/housekeeping_tasks
  def index
    authorize HousekeepingTask, :index?
    
    tasks = @current_hotel.housekeeping_tasks.includes(:room, :assigned_to)
    tasks = filter_tasks(tasks)
    
    @pagy, @tasks = pagy(tasks, limit: per_page, page: current_page)
    
    render json: {
      data: HousekeepingTaskBlueprint.render_as_hash(@tasks),
      meta: pagination_meta(@pagy)
    }
  end
  
  # GET /api/v1/hotels/:hotel_id/housekeeping_tasks/:id
  def show
    authorize @housekeeping_task
    
    render json: {
      data: HousekeepingTaskBlueprint.render_as_hash(@housekeeping_task, view: :detailed)
    }
  end
  
  # POST /api/v1/hotels/:hotel_id/housekeeping_tasks
  def create
    @housekeeping_task = @current_hotel.housekeeping_tasks.build(housekeeping_task_params)
    authorize @housekeeping_task
    
    if @housekeeping_task.save
      render json: {
        data: HousekeepingTaskBlueprint.render_as_hash(@housekeeping_task)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@housekeeping_task))
    end
  end
  
  # PATCH/PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id
  def update
    authorize @housekeeping_task
    
    # Check if user is housekeeping and restrict params
    permitted_params = if @current_account_user.has_role?(:housekeeping) && !@current_account_user.has_any_role?(:owner, :admin, :hotel_manager, :front_desk)
      params.permit(:status, :notes, :inspection_score, :started_at, :completed_at)
    else
      housekeeping_task_params
    end
    
    if @housekeeping_task.update(permitted_params)
      render json: {
        data: HousekeepingTaskBlueprint.render_as_hash(@housekeeping_task)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@housekeeping_task))
    end
  end
  
  # DELETE /api/v1/hotels/:hotel_id/housekeeping_tasks/:id
  def destroy
    authorize @housekeeping_task
    
    @housekeeping_task.destroy
    head :no_content
  end
  
  # PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/assign
  def assign
    authorize @housekeeping_task, :assign?
    
    unless params[:assigned_to_id].present?
      render json: {
        error: 'Bad Request',
        message: 'assigned_to_id is required'
      }, status: :bad_request
      return
    end
    
    # Find user within the current account
    user = @current_account.users.find(params[:assigned_to_id])
    
    if @housekeeping_task.update(assigned_to: user)
      render json: {
        data: HousekeepingTaskBlueprint.render_as_hash(@housekeeping_task)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@housekeeping_task))
    end
  rescue ActiveRecord::RecordNotFound
    render_not_found('User')
  end
  
  # PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/start
  def start
    authorize @housekeeping_task, :start?
    
    if @housekeeping_task.pending? || @housekeeping_task.cancelled?
      @housekeeping_task.start!
      render json: {
        data: HousekeepingTaskBlueprint.render_as_hash(@housekeeping_task)
      }
    else
      render json: {
        error: 'Conflict',
        message: 'Task must be in pending or cancelled status to start'
      }, status: :conflict
    end
  rescue => e
    handle_validation_error(ActiveRecord::RecordInvalid.new(@housekeeping_task))
  end
  
  # PUT /api/v1/hotels/:hotel_id/housekeeping_tasks/:id/complete
  def complete
    authorize @housekeeping_task, :complete?
    
    unless @housekeeping_task.in_progress?
      render json: {
        error: 'Conflict',
        message: 'Task must be in progress to complete'
      }, status: :conflict
      return
    end
    
    score = params[:inspection_score]
    
    # Validate inspection score if provided
    if score.present? && (score.to_i < 1 || score.to_i > 10)
      render json: {
        error: 'Bad Request',
        message: 'inspection_score must be between 1 and 10'
      }, status: :bad_request
      return
    end
    
    @housekeeping_task.complete!(score)
    render json: {
      data: HousekeepingTaskBlueprint.render_as_hash(@housekeeping_task)
    }
  rescue => e
    handle_validation_error(ActiveRecord::RecordInvalid.new(@housekeeping_task))
  end
  
  private
  
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end
  
  def set_housekeeping_task
    @housekeeping_task = @current_hotel.housekeeping_tasks.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('HousekeepingTask')
  end
  
  def filter_tasks(scope)
    # Filter by status
    scope = scope.where(status: params[:status]) if params[:status].present? && HousekeepingTask::STATUSES.include?(params[:status])
    
    # Filter by priority
    scope = scope.where(priority: params[:priority]) if params[:priority].present? && HousekeepingTask::PRIORITIES.include?(params[:priority])
    
    # Filter by date
    scope = scope.for_date(params[:date]) if params[:date].present?
    
    # Filter by room
    scope = scope.where(room_id: params[:room_id]) if params[:room_id].present?
    
    # Filter by assigned user
    scope = scope.where(assigned_to_id: params[:assigned_to_id]) if params[:assigned_to_id].present?
    
    # Sort
    case params[:sort]
    when 'priority'
      scope = scope.by_priority
    when 'scheduled'
      scope = scope.order(:scheduled_date, :scheduled_time)
    when 'room'
      scope = scope.joins(:room).order('rooms.room_number')
    else
      scope = scope.order(scheduled_date: :desc, scheduled_time: :asc)
    end
    
    scope
  end
  
  def housekeeping_task_params
    params.permit(
      :room_id, :task_type, :priority, :status,
      :scheduled_date, :scheduled_time, :assigned_to_id,
      :notes, :inspection_score, :started_at, :completed_at
    )
  end
end