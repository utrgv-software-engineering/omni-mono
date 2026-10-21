class Api::V1::MaintenanceRequestsController < Api::V1::BaseController
  before_action :set_hotel
  before_action :set_maintenance_request, only: [:show, :update, :destroy, :assign, :start, :complete, :cancel]
  
  # GET /api/v1/hotels/:hotel_id/maintenance_requests
  def index
    authorize MaintenanceRequest, :index?
    
    maintenance_requests = @current_hotel.maintenance_requests
                                        .includes(:room, :reported_by, :assigned_to)
    
    # Apply filters
    maintenance_requests = filter_maintenance_requests(maintenance_requests)
    
    # Apply pagination - using the helper methods from ApiPagination
    @pagy, @maintenance_requests = pagy(maintenance_requests.order(created_at: :desc), limit: per_page, page: current_page)
    
    render json: {
      data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_requests),
      meta: pagination_meta(@pagy),
      links: pagination_links(@pagy)
    }
  end
  
  # POST /api/v1/hotels/:hotel_id/maintenance_requests
  def create
    authorize MaintenanceRequest, :create?
    
    @maintenance_request = @current_hotel.maintenance_requests.build(maintenance_request_params)
    @maintenance_request.account = @current_account
    @maintenance_request.reported_by = @current_user
    @maintenance_request.status = 'open'
    
    if @maintenance_request.save
      render json: {
        data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_request)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@maintenance_request))
    end
  end
  
  # GET /api/v1/hotels/:hotel_id/maintenance_requests/:id
  def show
    authorize @maintenance_request, :show?
    
    render json: {
      data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_request)
    }
  end
  
  # PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id
  def update
    authorize @maintenance_request, :update?
    
    if @maintenance_request.update(update_maintenance_request_params)
      render json: {
        data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_request)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@maintenance_request))
    end
  end
  
  # DELETE /api/v1/hotels/:hotel_id/maintenance_requests/:id
  def destroy
    authorize @maintenance_request, :destroy?
    
    @maintenance_request.destroy
    head :no_content
  end
  
  # PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/assign
  def assign
    authorize @maintenance_request, :assign?
    
    assigned_to = params[:assigned_to_id].present? ? User.find_by(id: params[:assigned_to_id]) : nil
    
    if params[:assigned_to_id].present? && assigned_to.nil?
      render json: {
        error: 'Not Found',
        message: 'User not found'
      }, status: :not_found
      return
    end
    
    if @maintenance_request.update(assigned_to: assigned_to)
      render json: {
        data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_request)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@maintenance_request))
    end
  end
  
  # PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/start
  def start
    authorize @maintenance_request, :assign?
    
    unless @maintenance_request.open?
      render json: {
        error: 'Conflict',
        message: 'Request must be in open status to start work'
      }, status: :conflict
      return
    end
    
    @maintenance_request.start_work!
    
    render json: {
      data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_request)
    }
  rescue ActiveRecord::RecordInvalid => e
    handle_validation_error(e)
  end
  
  # PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/complete
  def complete
    authorize @maintenance_request, :complete?
    
    unless @maintenance_request.in_progress? || @maintenance_request.pending_parts?
      render json: {
        error: 'Conflict',
        message: 'Request must be in progress or pending_parts status to complete'
      }, status: :conflict
      return
    end
    
    resolution_notes = params[:resolution_notes]
    actual_cost = params[:actual_cost]
    
    @maintenance_request.complete!(
      resolution_notes: resolution_notes,
      actual_cost: actual_cost
    )
    
    render json: {
      data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_request)
    }
  rescue ActiveRecord::RecordInvalid => e
    handle_validation_error(e)
  end
  
  # PUT /api/v1/hotels/:hotel_id/maintenance_requests/:id/cancel
  def cancel
    authorize @maintenance_request, :cancel?
    
    if @maintenance_request.completed?
      render json: {
        error: 'Conflict',
        message: 'Cannot cancel completed request'
      }, status: :conflict
      return
    end
    
    reason = params[:reason]
    @maintenance_request.cancel!(reason)
    
    render json: {
      data: MaintenanceRequestBlueprint.render_as_hash(@maintenance_request)
    }
  rescue ActiveRecord::RecordInvalid => e
    handle_validation_error(e)
  end
  
  private
  
  def set_hotel
    @current_hotel = @current_account.hotels.find(params[:hotel_id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Hotel')
  end
  
  def set_maintenance_request
    @maintenance_request = @current_hotel.maintenance_requests.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('MaintenanceRequest')
  end
  
  def filter_maintenance_requests(scope)
    scope = scope.where(status: params[:status]) if params[:status].present?
    scope = scope.where(priority: params[:priority]) if params[:priority].present?
    scope = scope.where(category: params[:category]) if params[:category].present?
    scope = scope.where(room_id: params[:room_id]) if params[:room_id].present?
    scope = scope.where(assigned_to_id: params[:assigned_to_id]) if params[:assigned_to_id].present?
    scope = scope.where(scheduled_date: params[:scheduled_date]) if params[:scheduled_date].present?
    scope
  end
  
  def maintenance_request_params
    params.permit(
      :room_id, :category, :issue_description, :priority,
      :scheduled_date, :cost_estimate, :assigned_to_id
    )
  end
  
  def update_maintenance_request_params
    params.permit(
      :room_id, :category, :issue_description, :priority, :status,
      :scheduled_date, :cost_estimate, :actual_cost, :resolution_notes,
      :assigned_to_id
    )
  end
end