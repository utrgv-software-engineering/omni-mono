import type { JsonValue } from './index'

type ExpectFalse<Value extends false> = Value

type RejectFunction = ExpectFalse<(() => string) extends JsonValue ? true : false>
type RejectSymbol = ExpectFalse<symbol extends JsonValue ? true : false>

export type JsonValueTypeChecks = RejectFunction | RejectSymbol
