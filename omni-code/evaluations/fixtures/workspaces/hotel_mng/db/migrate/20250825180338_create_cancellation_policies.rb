class CreateCancellationPolicies < ActiveRecord::Migration[8.0]
  def change
    create_table :cancellation_policies do |t|
      t.references :account, null: false, foreign_key: true
      t.references :hotel, foreign_key: true
      t.string :name, null: false
      t.string :code, null: false
      t.text :description
      t.string :cancellation_type, null: false
      t.integer :free_cancellation_hours
      t.decimal :penalty_percentage, precision: 5, scale: 2
      t.integer :penalty_nights
      t.decimal :no_show_penalty, precision: 5, scale: 2
      t.boolean :is_refundable, default: true
      t.boolean :is_default, default: false

      t.timestamps
    end

    add_index :cancellation_policies, [:account_id, :hotel_id, :is_default], name: 'index_cancellation_policies_on_account_hotel_default'
  end
end
