import { Entity, Column, PrimaryGeneratedColumn } from 'typeorm';

@Entity('transactions')
export class Transaction {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  amount: number;

  @Column()
  currency: string;

  @Column()
  status: string;
}
