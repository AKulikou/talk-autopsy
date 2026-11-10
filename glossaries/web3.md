# Domain glossary: smart contracts and their testing (web3)

Starter list for smart-contract courses, converted on 2026-11-10 from the
owner's draft (all entries kept; grouped rows split into one term each, so
each works as a speech recognition key term). Used with `talk-autopsy run
<url-or-file> --domain web3`: merged with the per-video auto-glossary, these
entries win on a conflict. Names of people go to `glossaries/local/web3.md`
(git-ignored), never here.

Columns (format: `talk_autopsy/glossary.py`): `keep` yes = the term stays in
Latin script as it is; `ru` = the agreed Russian rendering; `notes` = context
for the models, including `heard as:` mis-recognitions (STT hints). `policy`
(translation-v3): `keep` Latin as is; `translate` Russian only; `both` Russian
with the English term in brackets at its first occurrence in the video, Russian
only afterwards; `ui` names as shown on screen. The policies map the draft's
decision column: оставить -> keep, перевести -> translate, оба -> both.

| term | keep | policy | ru | notes |
|---|---|---|---|---|
| smart contract | no | translate | смарт-контракт |  |
| Solidity | yes | keep |  | heard as: solidarity, solid city |
| Vyper | yes | keep |  | heard as: viper |
| EVM | yes | keep |  | heard as: E V M |
| Ethereum Virtual Machine | yes | keep |  |  |
| Ethereum | yes | keep |  | heard as: ether ium |
| ether | yes | keep |  |  |
| ETH | yes | keep |  |  |
| wei | yes | keep |  | heard as: way |
| gwei | yes | keep |  | heard as: g way, guay |
| gas | no | translate | газ | owner's draft: "gas fee" -> «газ (комиссия)» |
| gas limit | no | translate | лимит газа |  |
| gas price | no | translate | цена газа |  |
| mainnet | yes | keep |  | heard as: main net |
| testnet | yes | keep |  | heard as: test net |
| Sepolia | yes | keep |  | heard as: sepulia |
| L2 | yes | keep |  | heard as: L two |
| rollup | yes | keep |  | heard as: roll up |
| node | yes | keep |  |  |
| RPC | yes | keep |  | heard as: R P C |
| wallet | yes | keep |  | owner's draft also has «кошелёк (wallet)»; decision column says keep |
| MetaMask | yes | keep |  | heard as: meta mask |
| private key | no | translate | приватный ключ |  |
| transaction | no | translate | транзакция |  |
| tx | no | translate | транзакция |  |
| block | no | translate | блок |  |
| blockchain | no | translate | блокчейн |  |
| ABI | yes | keep |  | heard as: A B I |
| bytecode | yes | keep |  | heard as: byte code |
| opcode | yes | keep |  |  |
| keccak256 | yes | keep |  | heard as: ketchup, kecak |
| msg.sender | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: message sender |
| msg.value | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: message value |
| tx.origin | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: T X origin |
| address | yes | keep |  | Solidity keyword or identifier: write as in code |
| mapping | yes | keep |  | Solidity keyword or identifier: write as in code |
| struct | yes | keep |  | Solidity keyword or identifier: write as in code |
| enum | yes | keep |  | Solidity keyword or identifier: write as in code |
| uint256 | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: you int two fifty six |
| modifier | yes | keep |  | Solidity keyword or identifier: write as in code |
| onlyOwner | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: only owner |
| require | yes | keep |  | Solidity keyword or identifier: write as in code |
| revert | yes | keep |  | Solidity keyword or identifier: write as in code |
| assert | yes | keep |  | Solidity keyword or identifier: write as in code |
| storage | yes | keep |  | Solidity keyword or identifier: write as in code |
| memory | yes | keep |  | Solidity keyword or identifier: write as in code |
| calldata | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: call data |
| payable | yes | keep |  | Solidity keyword or identifier: write as in code |
| view | yes | keep |  | Solidity keyword or identifier: write as in code |
| pure | yes | keep |  | Solidity keyword or identifier: write as in code |
| external | yes | keep |  | Solidity keyword or identifier: write as in code |
| internal | yes | keep |  | Solidity keyword or identifier: write as in code |
| public | yes | keep |  | Solidity keyword or identifier: write as in code |
| private | yes | keep |  | Solidity keyword or identifier: write as in code |
| constructor | yes | keep |  | Solidity keyword or identifier: write as in code |
| fallback | yes | keep |  | Solidity keyword or identifier: write as in code |
| receive | yes | keep |  | Solidity keyword or identifier: write as in code |
| delegatecall | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: delegate call |
| call | yes | keep |  | Solidity keyword or identifier: write as in code |
| staticcall | yes | keep |  | Solidity keyword or identifier: write as in code |
| selfdestruct | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: self destruct |
| event | yes | keep |  | Solidity keyword or identifier: write as in code |
| emit | yes | keep |  | Solidity keyword or identifier: write as in code |
| immutable | yes | keep |  | Solidity keyword or identifier: write as in code |
| constant | yes | keep |  | Solidity keyword or identifier: write as in code |
| ERC-20 | yes | keep |  | heard as: E R C twenty, ERC 20 |
| ERC-721 | yes | keep |  |  |
| ERC-1155 | yes | keep |  |  |
| ERC-4626 | yes | keep |  |  |
| EIP | yes | keep |  | heard as: E I P |
| OpenZeppelin | yes | keep |  | heard as: open zeppelin, open Zeplin |
| Chainlink | yes | keep |  | heard as: chain link |
| oracle | no | translate | оракул |  |
| proxy | no | both | прокси |  |
| upgradeable | no | both | обновляемый |  |
| UUPS | yes | keep |  | heard as: U U P S |
| transparent proxy | yes | keep |  |  |
| multisig | yes | keep |  | heard as: multi sig |
| Safe | yes | keep |  |  |
| DeFi | yes | keep |  | heard as: defy |
| DEX | yes | keep |  | heard as: D E X |
| AMM | yes | keep |  |  |
| Uniswap | yes | keep |  | heard as: uni swap |
| Aave | yes | keep |  | heard as: ah vay |
| NFT | yes | keep |  |  |
| Foundry | yes | keep |  |  |
| forge | yes | keep |  | a command (forge test), not the ordinary word |
| cast | yes | keep |  | a command, not the ordinary word |
| anvil | yes | keep |  | a command, not the ordinary word |
| chisel | yes | keep |  | a command, not the ordinary word |
| Hardhat | yes | keep |  | heard as: hard hat |
| Remix | yes | keep |  |  |
| Truffle | yes | keep |  |  |
| Ganache | yes | keep |  | a tool, not the food |
| Slither | yes | keep |  | heard as: slider |
| Echidna | yes | keep |  | heard as: a kidna |
| Medusa | yes | keep |  |  |
| Mythril | yes | keep |  | heard as: mithril |
| Halmos | yes | keep |  | heard as: hal moss |
| Certora | yes | keep |  | heard as: sertora |
| Aderyn | yes | keep |  | heard as: aderin |
| Etherscan | yes | keep |  | heard as: ether scan |
| Tenderly | yes | keep |  |  |
| unit test | no | translate | юнит-тест |  |
| integration test | no | translate | интеграционный тест |  |
| fork test | no | both | форк-тест |  |
| mainnet fork | no | both | форк основной сети |  |
| fuzzing | yes | keep |  | owner's draft also has «фаззинг (fuzzing)»; still open (fuzzing / фаззинг) |
| fuzz test | yes | keep |  |  |
| stateful fuzzing | yes | keep |  |  |
| stateless fuzzing | yes | keep |  |  |
| invariant | no | both | инвариант | still open with the owner (invariant / инвариант) |
| invariant test | no | both | тест инвариантов | Russian added in conversion, not in the draft: owner to check |
| property-based testing | no | both | тестирование свойств |  |
| formal verification | no | translate | формальная верификация |  |
| symbolic execution | no | translate | символьное исполнение |  |
| static analysis | no | translate | статический анализ |  |
| coverage | no | both | покрытие |  |
| reentrancy | yes | keep |  | owner's draft: «повторный вход (reentrancy)» at the first mention |
| front-running | yes | keep |  |  |
| MEV | yes | keep |  |  |
| access control | no | translate | контроль доступа |  |
| integer overflow | no | both | переполнение |  |
| integer underflow | no | both | переполнение снизу | Russian added in conversion, not in the draft: owner to check |
| denial of service | yes | keep |  |  |
| DoS | yes | keep |  |  |
| oracle manipulation | no | translate | манипуляция оракулом |  |
| flash loan | yes | keep |  | owner's draft also has «флэш-кредит (flash loan)» |
| rug pull | yes | keep |  |  |
| audit | no | translate | аудит |  |
| auditor | no | translate | аудитор |  |
| finding | no | translate | находка | an audit finding |
| severity | no | translate | критичность | of a finding |
| PoC | yes | keep |  |  |
| proof of concept | yes | keep |  |  |
| bug bounty | yes | keep |  |  |
| Code4rena | yes | keep |  | heard as: code arena |
| Sherlock | yes | keep |  | audit platform |
| Immunefi | yes | keep |  | heard as: immune fi |
| Cantina | yes | keep |  | audit platform |
