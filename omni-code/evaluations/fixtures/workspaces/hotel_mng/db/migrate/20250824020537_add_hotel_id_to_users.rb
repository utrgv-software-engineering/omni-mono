class AddHotelIdToUsers < ActiveRecord::Migration[8.0]
  def change
    add_reference :users, :hotel, null: false, foreign_key: true
  end
end
