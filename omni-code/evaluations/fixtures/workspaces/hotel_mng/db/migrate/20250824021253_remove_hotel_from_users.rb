class RemoveHotelFromUsers < ActiveRecord::Migration[8.0]
  def change
    remove_reference :users, :hotel, foreign_key: true
  end
end
