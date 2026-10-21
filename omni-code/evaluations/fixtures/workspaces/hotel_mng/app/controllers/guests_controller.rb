# frozen_string_literal: true

class GuestsController < StaffController
  before_action :set_guest, only: [:show, :edit, :update, :destroy]
  
  def index
    @guests = policy_scope(Guest)
    authorize Guest
    
    if params[:search].present?
      search_term = "%#{params[:search]}%"
      @guests = @guests.where("first_name ILIKE ? OR last_name ILIKE ? OR email ILIKE ?", 
                              search_term, search_term, search_term)
    end
    
    @pagy, @guests = pagy(@guests.order(:last_name, :first_name))
  end
  
  def show
    authorize @guest
    @reservations = @guest.reservations.includes(:hotel, :room).order(check_in_date: :desc)
  end
  
  def new
    @guest = Guest.new
    authorize @guest
  end
  
  def create
    @guest = Guest.new(guest_params)
    authorize @guest
    
    if @guest.save
      redirect_to @guest, notice: 'Guest was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @guest
  end
  
  def update
    authorize @guest
    if @guest.update(guest_params)
      redirect_to @guest, notice: 'Guest was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @guest
    @guest.destroy
    redirect_to guests_path, notice: 'Guest was successfully destroyed.'
  end
  
  private
  
  def set_guest
    @guest = Guest.find(params[:id])
  end
  
  def guest_params
    params.require(:guest).permit(
      :first_name, :last_name, :email, :phone, :date_of_birth,
      :nationality, :passport_number, :id_number, :address, :city,
      :state, :country, :postal_code, :vip_status, :notes
    )
  end
end