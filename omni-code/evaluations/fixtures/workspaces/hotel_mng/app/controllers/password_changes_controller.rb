class PasswordChangesController < ApplicationController
  before_action :authenticate_user!
  skip_before_action :check_force_password_change

  def edit
  end

  def update
    if params[:password].blank?
      flash.now[:alert] = "Password can't be blank."
      render :edit, status: :unprocessable_entity
      return
    end

    if params[:password] != params[:password_confirmation]
      flash.now[:alert] = "Password confirmation doesn't match."
      render :edit, status: :unprocessable_entity
      return
    end

    if params[:password].length < 6
      flash.now[:alert] = "Password must be at least 6 characters."
      render :edit, status: :unprocessable_entity
      return
    end

    current_user.update!(
      password: params[:password],
      password_confirmation: params[:password_confirmation],
      force_password_change: false
    )

    flash[:notice] = "Password updated successfully."
    redirect_to root_path
  end
end
