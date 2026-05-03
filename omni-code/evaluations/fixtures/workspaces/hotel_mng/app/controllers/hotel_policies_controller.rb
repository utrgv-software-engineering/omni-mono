# frozen_string_literal: true

class HotelPoliciesController < StaffController
  before_action :set_hotel
  before_action :set_hotel_policy, only: [:show, :edit, :update, :destroy]
  
  def index
    @hotel_policies = @hotel.hotel_policies
    authorize @hotel, :show?
  end
  
  def show
    authorize @hotel, :show?
  end
  
  def new
    @hotel_policy = @hotel.hotel_policies.build
    authorize @hotel, :update?
  end
  
  def create
    @hotel_policy = @hotel.hotel_policies.build(hotel_policy_params)
    authorize @hotel, :update?
    
    if @hotel_policy.save
      redirect_to hotel_hotel_policy_path(@hotel, @hotel_policy), notice: 'Hotel policy was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @hotel, :update?
  end
  
  def update
    authorize @hotel, :update?
    if @hotel_policy.update(hotel_policy_params)
      redirect_to hotel_hotel_policy_path(@hotel, @hotel_policy), notice: 'Hotel policy was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @hotel, :update?
    @hotel_policy.destroy
    redirect_to hotel_hotel_policies_path(@hotel), notice: 'Hotel policy was successfully destroyed.'
  end
  
  private
  
  def set_hotel
    @hotel = Hotel.find(params[:hotel_id])
  end
  
  def set_hotel_policy
    @hotel_policy = @hotel.hotel_policies.find(params[:id])
  end
  
  def hotel_policy_params
    params.require(:hotel_policy).permit(
      :policy_type, :title, :description, :sort_order, :is_active
    )
  end
end