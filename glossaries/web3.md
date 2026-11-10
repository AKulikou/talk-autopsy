# Domain glossary: smart contracts and their testing (web3)

Starter list for smart-contract courses, converted on 2026-11-10 from the
owner's draft (all entries kept; grouped rows split into one term each, so
each works as a speech recognition key term). Used with `talk-autopsy run
<url-or-file> --domain web3`: merged with the per-video auto-glossary, these
entries win on a conflict. Names of people go to `glossaries/local/web3.md`
(git-ignored), never here.

Columns (format: `talk_autopsy/glossary.py`): `keep` yes = the term stays in
Latin script as it is; `ru` = the agreed Russian rendering; `notes` = context
for the models, including `heard as:` mis-recognitions (STT hints).

| term | keep | ru | notes |
|---|---|---|---|
| smart contract | no | смарт-контракт |  |
| Solidity | yes |  | heard as: solidarity, solid city |
| Vyper | yes |  | heard as: viper |
| EVM | yes |  | heard as: E V M |
| Ethereum Virtual Machine | yes |  |  |
| Ethereum | yes |  | heard as: ether ium |
| ether | yes |  |  |
| ETH | yes |  |  |
| wei | yes |  | heard as: way |
| gwei | yes |  | heard as: g way, guay |
| gas | no | газ | owner's draft: "gas fee" -> «газ (комиссия)» |
| gas limit | no | лимит газа |  |
| gas price | no | цена газа |  |
| mainnet | yes |  | heard as: main net |
| testnet | yes |  | heard as: test net |
| Sepolia | yes |  | heard as: sepulia |
| L2 | yes |  | heard as: L two |
| rollup | yes |  | heard as: roll up |
| node | yes |  |  |
| RPC | yes |  | heard as: R P C |
| wallet | yes |  | owner's draft also has «кошелёк (wallet)»; decision column says keep |
| MetaMask | yes |  | heard as: meta mask |
| private key | no | приватный ключ |  |
| transaction | no | транзакция |  |
| tx | no | транзакция |  |
| block | no | блок |  |
| blockchain | no | блокчейн |  |
| ABI | yes |  | heard as: A B I |
| bytecode | yes |  | heard as: byte code |
| opcode | yes |  |  |
| keccak256 | yes |  | heard as: ketchup, kecak |
| msg.sender | yes |  | Solidity keyword or identifier: write as in code; heard as: message sender |
| msg.value | yes |  | Solidity keyword or identifier: write as in code; heard as: message value |
| tx.origin | yes |  | Solidity keyword or identifier: write as in code; heard as: T X origin |
| address | yes |  | Solidity keyword or identifier: write as in code |
| mapping | yes |  | Solidity keyword or identifier: write as in code |
| struct | yes |  | Solidity keyword or identifier: write as in code |
| enum | yes |  | Solidity keyword or identifier: write as in code |
| uint256 | yes |  | Solidity keyword or identifier: write as in code; heard as: you int two fifty six |
| modifier | yes |  | Solidity keyword or identifier: write as in code |
| onlyOwner | yes |  | Solidity keyword or identifier: write as in code; heard as: only owner |
| require | yes |  | Solidity keyword or identifier: write as in code |
| revert | yes |  | Solidity keyword or identifier: write as in code |
| assert | yes |  | Solidity keyword or identifier: write as in code |
| storage | yes |  | Solidity keyword or identifier: write as in code |
| memory | yes |  | Solidity keyword or identifier: write as in code |
| calldata | yes |  | Solidity keyword or identifier: write as in code; heard as: call data |
| payable | yes |  | Solidity keyword or identifier: write as in code |
| view | yes |  | Solidity keyword or identifier: write as in code |
| pure | yes |  | Solidity keyword or identifier: write as in code |
| external | yes |  | Solidity keyword or identifier: write as in code |
| internal | yes |  | Solidity keyword or identifier: write as in code |
| public | yes |  | Solidity keyword or identifier: write as in code |
| private | yes |  | Solidity keyword or identifier: write as in code |
| constructor | yes |  | Solidity keyword or identifier: write as in code |
| fallback | yes |  | Solidity keyword or identifier: write as in code |
| receive | yes |  | Solidity keyword or identifier: write as in code |
| delegatecall | yes |  | Solidity keyword or identifier: write as in code; heard as: delegate call |
| call | yes |  | Solidity keyword or identifier: write as in code |
| staticcall | yes |  | Solidity keyword or identifier: write as in code |
| selfdestruct | yes |  | Solidity keyword or identifier: write as in code; heard as: self destruct |
| event | yes |  | Solidity keyword or identifier: write as in code |
| emit | yes |  | Solidity keyword or identifier: write as in code |
| immutable | yes |  | Solidity keyword or identifier: write as in code |
| constant | yes |  | Solidity keyword or identifier: write as in code |
| ERC-20 | yes |  | heard as: E R C twenty, ERC 20 |
| ERC-721 | yes |  |  |
| ERC-1155 | yes |  |  |
| ERC-4626 | yes |  |  |
| EIP | yes |  | heard as: E I P |
| OpenZeppelin | yes |  | heard as: open zeppelin, open Zeplin |
| Chainlink | yes |  | heard as: chain link |
| oracle | no | оракул |  |
| proxy | no | прокси |  |
| upgradeable | no | обновляемый |  |
| UUPS | yes |  | heard as: U U P S |
| transparent proxy | yes |  |  |
| multisig | yes |  | heard as: multi sig |
| Safe | yes |  |  |
| DeFi | yes |  | heard as: defy |
| DEX | yes |  | heard as: D E X |
| AMM | yes |  |  |
| Uniswap | yes |  | heard as: uni swap |
| Aave | yes |  | heard as: ah vay |
| NFT | yes |  |  |
| Foundry | yes |  |  |
| forge | yes |  | a command (forge test), not the ordinary word |
| cast | yes |  | a command, not the ordinary word |
| anvil | yes |  | a command, not the ordinary word |
| chisel | yes |  | a command, not the ordinary word |
| Hardhat | yes |  | heard as: hard hat |
| Remix | yes |  |  |
| Truffle | yes |  |  |
| Ganache | yes |  | a tool, not the food |
| Slither | yes |  | heard as: slider |
| Echidna | yes |  | heard as: a kidna |
| Medusa | yes |  |  |
| Mythril | yes |  | heard as: mithril |
| Halmos | yes |  | heard as: hal moss |
| Certora | yes |  | heard as: sertora |
| Aderyn | yes |  | heard as: aderin |
| Etherscan | yes |  | heard as: ether scan |
| Tenderly | yes |  |  |
| unit test | no | юнит-тест |  |
| integration test | no | интеграционный тест |  |
| fork test | no | форк-тест |  |
| mainnet fork | no | форк основной сети |  |
| fuzzing | yes |  | owner's draft also has «фаззинг (fuzzing)»; still open (fuzzing / фаззинг) |
| fuzz test | yes |  |  |
| stateful fuzzing | yes |  |  |
| stateless fuzzing | yes |  |  |
| invariant | no | инвариант | still open with the owner (invariant / инвариант) |
| invariant test | no | тест инвариантов | Russian added in conversion, not in the draft: owner to check |
| property-based testing | no | тестирование свойств |  |
| formal verification | no | формальная верификация |  |
| symbolic execution | no | символьное исполнение |  |
| static analysis | no | статический анализ |  |
| coverage | no | покрытие |  |
| reentrancy | yes |  | owner's draft: «повторный вход (reentrancy)» at the first mention |
| front-running | yes |  |  |
| MEV | yes |  |  |
| access control | no | контроль доступа |  |
| integer overflow | no | переполнение |  |
| integer underflow | no | переполнение снизу | Russian added in conversion, not in the draft: owner to check |
| denial of service | yes |  |  |
| DoS | yes |  |  |
| oracle manipulation | no | манипуляция оракулом |  |
| flash loan | yes |  | owner's draft also has «флэш-кредит (flash loan)» |
| rug pull | yes |  |  |
| audit | no | аудит |  |
| auditor | no | аудитор |  |
| finding | no | находка | an audit finding |
| severity | no | критичность | of a finding |
| PoC | yes |  |  |
| proof of concept | yes |  |  |
| bug bounty | yes |  |  |
| Code4rena | yes |  | heard as: code arena |
| Sherlock | yes |  | audit platform |
| Immunefi | yes |  | heard as: immune fi |
| Cantina | yes |  | audit platform |
