class Api::V1::CheckOutsController < Api::V1::BaseController
  before_action :set_reservation
  
  # GET /api/v1/reservations/:reservation_id/check_out/summary
  def summary
    authorize @reservation, :check_out?
    
    # Validate reservation can be checked out
    unless @reservation.status == 'checked_in'
      render json: {
        error: 'Conflict',
        message: 'Only checked-in reservations can be checked out'
      }, status: :conflict
      return
    end
    
    # Calculate any pending charges
    pending_charges = calculate_pending_charges
    balance_due = @reservation.total_amount - (@reservation.paid_amount || 0)
    
    render json: {
      data: {
        reservation: ReservationBlueprint.render_as_hash(@reservation),
        pending_charges: pending_charges,
        balance_due: balance_due.to_f,
        total_amount: @reservation.total_amount.to_s,
        paid_amount: (@reservation.paid_amount || 0).to_f
      }
    }
  end
  
  # POST /api/v1/reservations/:reservation_id/check_out
  def create
    authorize @reservation, :check_out?
    
    # Validate reservation can be checked out
    unless @reservation.status == 'checked_in'
      render json: {
        error: 'Conflict',
        message: 'Only checked-in reservations can be checked out'
      }, status: :conflict
      return
    end
    
    # Update reservation status and check-out time
    @reservation.status = 'checked_out'
    @reservation.actual_check_out = Time.current
    
    # Process payment if provided
    if params[:payment_amount].present?
      payment_amount = params[:payment_amount].to_f
      
      if payment_amount < 0
        render json: {
          error: 'Bad Request',
          message: 'Payment amount cannot be negative'
        }, status: :bad_request
        return
      end
      
      @reservation.paid_amount = (@reservation.paid_amount || 0) + payment_amount
      @reservation.payment_status = @reservation.paid_amount >= @reservation.total_amount ? 'paid' : 'partial'
    end
    
    # Update room status
    if @reservation.room
      @reservation.room.status = 'cleaning'
      @reservation.room.save!
      
      # Create housekeeping task if enabled
      if params[:create_housekeeping_task].to_s == 'true' || params[:create_housekeeping_task] == true
        create_housekeeping_task(@reservation.room)
      end
    end
    
    if @reservation.save
      render json: {
        data: ReservationBlueprint.render_as_hash(@reservation),
        message: 'Guest successfully checked out'
      }, status: :ok
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@reservation))
    end
  end
  
  private
  
  def set_reservation
    @reservation = Reservation.find(params[:reservation_id])
    
    # Ensure reservation belongs to current account through hotel
    unless @current_account.hotels.exists?(id: @reservation.hotel_id)
      render_not_found('Reservation')
    end
  rescue ActiveRecord::RecordNotFound
    render_not_found('Reservation')
  end
  
  def calculate_pending_charges
    # This would calculate minibar, services, late checkout fees etc.
    # For now, return empty array
    []
  end
  
  def create_housekeeping_task(room)
    # Create a housekeeping task for the room
    task = room.housekeeping_tasks.create(
      hotel: room.hotel,
      account: @current_account,
      task_type: 'deep_clean',
      priority: 'high',
      status: 'pending',
      notes: "Check-out cleaning for #{@reservation.guest.full_name}",
      scheduled_date: Date.current
    )
    task
  rescue => e
    # Log error but don't fail the checkout
    Rails.logger.error "Failed to create housekeeping task: #{e.message}"
    nil
  end
end