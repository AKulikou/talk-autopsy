# Domain glossary v2: smart contracts, Solidity and Foundry (web3-v2)

Dictionary v2 for E1 (v1.2, docs/experiments.md): built on 2026-11-14 from the owner's review of
every row (work/web3-v2-selection.md, local) of the current `web3.md`, the owner's real runs, lessons
1-10 of the owner's course and, as a hint for Russian forms only, the headings of the ethereum.org
Russian glossary (https://ethereum.org/ru/glossary/, CC BY 4.0, ethereum.org contributors): only term
names and our own renderings are here, no definition or text. Used with `--domain web3-v2`; the
current `web3.md` stays the default until E1 decides. Columns as in `web3.md` (format:
`talk_autopsy/glossary.py`).

| term | keep | policy | ru | notes |
|---|---|---|---|---|
| storage | no | both | хранилище |  |
| memory | no | both | память |  |
| wallet | no | translate | кошелёк |  |
| testnet | no | both | тестовая сеть | heard as: test net |
| mainnet | no | both | основная сеть | heard as: main net |
| node | no | both | нода |  |
| rollup | no | both | роллап | heard as: roll up |
| reentrancy | no | both | повторный вход |  |
| fuzzing | no | both | фаззинг |  |
| fuzz test | no | both | фазз-тест |  |
| flash loan | no | both | флэш-кредит |  |
| denial of service | no | both | отказ в обслуживании |  |
| forge test | yes | keep |  | write exactly as in code or in the terminal |
| forge build | yes | keep |  | write exactly as in code or in the terminal |
| forge script | yes | keep |  | write exactly as in code or in the terminal |
| forge coverage | yes | keep |  | write exactly as in code or in the terminal |
| forge init | yes | keep |  | write exactly as in code or in the terminal |
| forge install | yes | keep |  | write exactly as in code or in the terminal |
| forge fmt | yes | keep |  | write exactly as in code or in the terminal |
| forge snapshot | yes | keep |  | write exactly as in code or in the terminal |
| cast send | yes | keep |  | write exactly as in code or in the terminal |
| cast call | yes | keep |  | write exactly as in code or in the terminal |
| cast wallet | yes | keep |  | write exactly as in code or in the terminal |
| foundryup | yes | keep |  | write exactly as in code or in the terminal |
| foundry.toml | yes | keep |  | write exactly as in code or in the terminal |
| forge-std | yes | keep |  | write exactly as in code or in the terminal |
| vm.prank | yes | keep |  | write exactly as in code or in the terminal |
| vm.startPrank | yes | keep |  | write exactly as in code or in the terminal |
| vm.stopPrank | yes | keep |  | write exactly as in code or in the terminal |
| vm.deal | yes | keep |  | write exactly as in code or in the terminal |
| vm.expectRevert | yes | keep |  | write exactly as in code or in the terminal |
| vm.expectEmit | yes | keep |  | write exactly as in code or in the terminal |
| vm.warp | yes | keep |  | write exactly as in code or in the terminal |
| vm.roll | yes | keep |  | write exactly as in code or in the terminal |
| vm.startBroadcast | yes | keep |  | write exactly as in code or in the terminal |
| vm.stopBroadcast | yes | keep |  | write exactly as in code or in the terminal |
| makeAddr | yes | keep |  | write exactly as in code or in the terminal |
| hoax | yes | keep |  | write exactly as in code or in the terminal |
| setUp | yes | keep |  | write exactly as in code or in the terminal |
| assertEq | yes | keep |  | write exactly as in code or in the terminal |
| console.log | yes | keep |  | write exactly as in code or in the terminal |
| cheatcode | no | both | чит-код |  |
| broadcast | yes | keep |  | write exactly as in code or in the terminal |
| --rpc-url | yes | keep |  | write exactly as in code or in the terminal |
| --private-key | yes | keep |  | write exactly as in code or in the terminal |
| SPDX-License-Identifier | yes | keep |  | write exactly as in code |
| abi.encode | yes | keep |  | write exactly as in code |
| abi.encodePacked | yes | keep |  | write exactly as in code |
| abi.decode | yes | keep |  | write exactly as in code |
| block.timestamp | yes | keep |  | write exactly as in code |
| block.number | yes | keep |  | write exactly as in code |
| bytes32 | yes | keep |  | write exactly as in code |
| int256 | yes | keep |  | write exactly as in code |
| uint8 | yes | keep |  | write exactly as in code |
| Ownable | yes | keep |  | write exactly as in code |
| ERC721URIStorage | yes | keep |  | write exactly as in code |
| tokenURI | yes | keep |  | write exactly as in code |
| balanceOf | yes | keep |  | write exactly as in code |
| totalSupply | yes | keep |  | write exactly as in code |
| transferFrom | yes | keep |  | write exactly as in code |
| approve | yes | keep |  | write exactly as in code |
| function selector | no | both | селектор функции |  |
| function signature | no | both | сигнатура функции |  |
| state variable | no | translate | переменная состояния |  |
| visibility | no | translate | видимость |  |
| inheritance | no | translate | наследование |  |
| interface | no | translate | интерфейс |  |
| library | no | translate | библиотека |  |
| compiler | no | translate | компилятор |  |
| deploy | no | translate | деплоить |  |
| deployment | no | translate | деплой |  |
| account | no | translate | аккаунт |  |
| EOA | yes | keep |  |  |
| externally owned account | no | both | внешний аккаунт |  |
| contract account | no | translate | контрактный аккаунт |  |
| nonce | yes | keep |  |  |
| hash | no | translate | хеш |  |
| hashing | no | translate | хеширование |  |
| SHA-256 | yes | keep |  |  |
| signature | no | translate | подпись |  |
| digital signature | no | translate | цифровая подпись |  |
| signed transaction | no | translate | подписанная транзакция |  |
| public key | no | translate | публичный ключ |  |
| seed phrase | no | translate | сид-фраза |  |
| consensus | no | translate | консенсус |  |
| proof of stake | no | both | доказательство доли владения |  |
| proof of work | no | both | доказательство выполнения работы |  |
| PoS | yes | keep |  |  |
| PoW | yes | keep |  |  |
| validator | no | translate | валидатор |  |
| staking | no | translate | стейкинг |  |
| mining | no | translate | майнинг |  |
| miner | no | translate | майнер |  |
| block explorer | no | both | обозреватель блоков |  |
| finality | no | translate | финальность |  |
| fork | no | translate | форк |  |
| hard fork | no | translate | хардфорк |  |
| genesis block | no | translate | генезис-блок |  |
| block reward | no | translate | вознаграждение за блок |  |
| block time | no | translate | время блока |  |
| mempool | yes | keep |  |  |
| Merkle tree | no | translate | дерево Меркла |  |
| decentralization | no | translate | децентрализация |  |
| decentralized | no | translate | децентрализованный |  |
| peer-to-peer | no | both | одноранговый |  |
| dapp | yes | keep |  |  |
| DAO | yes | keep |  |  |
| token | no | translate | токен |  |
| stablecoin | no | translate | стейблкоин |  |
| wrapped token | no | translate | обёрнутый токен |  |
| mint | no | both | выпустить |  |
| burn | no | translate | сжечь |  |
| zero address | no | translate | нулевой адрес |  |
| liquidity | no | translate | ликвидность |  |
| bridge | no | translate | мост |  |
| sidechain | no | translate | сайдчейн |  |
| onchain | no | translate | ончейн |  |
| offchain | no | translate | офчейн |  |
| faucet | no | both | кран |  |
| base fee | no | translate | базовая комиссия |  |
| priority fee | no | translate | комиссия за приоритет |  |
| transaction fee | no | translate | комиссия за транзакцию |  |
| gas fee | no | translate | комиссия за газ |  |
| Turing complete | no | translate | тьюринг-полный |  |
| cryptography | no | translate | криптография |  |
| ECDSA | yes | keep |  |  |
| state | no | translate | состояние |  |
| permissionless | no | translate | без разрешений |  |
| trustless | no | both | не требующий доверия |  |
| decentralized exchange | no | both | децентрализованная биржа |  |
| escrow | no | both | эскроу |  |
| web3 | yes | keep |  |  |
| test-driven development | no | both | разработка через тестирование |  |
| snapshot | no | translate | снимок |  |
| mock | no | both | заглушка |  |
| staging test | no | both | тест на тестовой сети |  |
| Makefile | yes | keep |  |  |
| .env | yes | keep |  |  |
| RPC URL | yes | keep |  |  |
| chain ID | yes | keep |  |  |
| ethers.js | yes | keep |  |  |
| Alchemy | yes | keep |  |  |
| Infura | yes | keep |  |  |
| audit finding | no | translate | находка аудита |  |
| Bitcoin | yes | keep |  |  |
| Arbitrum | yes | keep |  |  |
| Optimism | yes | keep |  |  |
| Polygon | yes | keep |  |  |
| Polygon zkEVM | yes | keep |  |  |
| zkSync | yes | keep |  |  |
| Linea | yes | keep |  |  |
| Avalanche | yes | keep |  |  |
| Solana | yes | keep |  |  |
| Polkadot | yes | keep |  |  |
| Fantom | yes | keep |  |  |
| Harmony | yes | keep |  |  |
| Ethereum Classic | yes | keep |  |  |
| MATIC | yes | keep |  |  |
| Goerli | yes | keep |  |  |
| Chainlink VRF | yes | keep |  |  |
| CryptoPunks | yes | keep |  |  |
| Bored Apes | yes | keep |  |  |
| GitHub | yes | keep |  |  |
| GitHub Discussions | yes | keep |  |  |
| Ethereum Stack Exchange | yes | keep |  |  |
| Cyfrin | yes | keep |  |  |
| EIP-1559 | yes | keep |  |  |
| ETH2 | yes | keep |  |  |
| Keccak-256 | yes | keep |  |  |
| Layer 1 | yes | keep |  |  |
| Layer 2 | yes | keep |  |  |
| 51% attack | no | translate | атака 51% |  |
| Sybil attack | no | translate | атака Сивиллы |  |
| Sybil resistance | no | translate | защита от атаки Сивиллы |  |
| Nakamoto consensus | no | translate | консенсус Накамото |  |
| decentralized oracle network | no | both | децентрализованная сеть оракулов |  |
| hybrid smart contract | no | translate | гибридный смарт-контракт |  |
| trust-minimized | no | translate | с минимальным доверием |  |
| trust-minimized agreement | no | translate | соглашение с минимальным доверием |  |
| transaction hash | no | translate | хеш транзакции |  |
| hash function | no | translate | хеш-функция |  |
| previous hash | no | translate | предыдущий хеш |  |
| block confirmations | no | translate | подтверждения блока |  |
| mnemonic phrase | no | translate | мнемоническая фраза |  |
| secret recovery phrase | yes | ui | секретная фраза восстановления | shown on screen |
| account details | yes | ui | сведения об аккаунте | shown on screen |
| max priority fee | no | translate | максимальная комиссия за приоритет |  |
| input data | no | translate | входные данные |  |
| node operator | no | translate | оператор узла |  |
| non-fungible token | no | translate | невзаимозаменяемый токен |  |
| decentralized application | no | translate | децентрализованное приложение |  |
| decentralized ledger | no | translate | децентрализованный реестр |  |
| immutability | no | translate | неизменяемость |  |
| cryptocurrency | no | translate | криптовалюта |  |
| store of value | no | translate | средство сбережения |  |
| counterparty risk | no | translate | риск контрагента |  |
| clearinghouse | no | translate | клиринговая палата |  |
| money market | no | translate | денежный рынок |  |
| whitepaper | no | both | вайтпейпер |  |
| message signature | no | translate | подпись сообщения |  |
| Injected Provider | yes | ui | подключённый кошелёк | shown on screen |
| Deploy & Run Transactions | yes | ui | развёртывание и запуск транзакций | shown on screen |
| Solidity Compiler | yes | ui | компилятор Solidity | shown on screen |
| upgradeable | no | both | обновляемый |  |
| audit | no | translate | аудит |  |
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
| Sepolia | yes | keep |  | heard as: sepulia |
| L2 | yes | keep |  | heard as: L two |
| RPC | yes | keep |  | heard as: R P C |
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
| mapping | yes | keep |  | Solidity keyword or identifier: write as in code |
| struct | yes | keep |  | Solidity keyword or identifier: write as in code |
| enum | yes | keep |  | Solidity keyword or identifier: write as in code |
| uint256 | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: you int two fifty six |
| modifier | yes | keep |  | Solidity keyword or identifier: write as in code |
| onlyOwner | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: only owner |
| calldata | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: call data |
| payable | yes | keep |  | Solidity keyword or identifier: write as in code |
| delegatecall | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: delegate call |
| staticcall | yes | keep |  | Solidity keyword or identifier: write as in code |
| selfdestruct | yes | keep |  | Solidity keyword or identifier: write as in code; heard as: self destruct |
| ERC-20 | yes | keep |  | heard as: E R C twenty, ERC 20 |
| ERC-721 | yes | keep |  |  |
| ERC-1155 | yes | keep |  |  |
| ERC-4626 | yes | keep |  |  |
| EIP | yes | keep |  | heard as: E I P |
| OpenZeppelin | yes | keep |  | heard as: open zeppelin, open Zeplin |
| Chainlink | yes | keep |  | heard as: chain link |
| oracle | no | translate | оракул |  |
| proxy | no | both | прокси |  |
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
| stateful fuzzing | yes | keep |  |  |
| stateless fuzzing | yes | keep |  |  |
| invariant | no | both | инвариант | still open with the owner (invariant / инвариант) |
| invariant test | no | both | тест инвариантов | Russian added in conversion, not in the draft: owner to check |
| property-based testing | no | both | тестирование свойств |  |
| formal verification | no | translate | формальная верификация |  |
| symbolic execution | no | translate | символьное исполнение |  |
| static analysis | no | translate | статический анализ |  |
| coverage | no | both | покрытие |  |
| front-running | yes | keep |  |  |
| MEV | yes | keep |  |  |
| access control | no | translate | контроль доступа |  |
| integer overflow | no | both | переполнение |  |
| integer underflow | no | both | переполнение снизу | Russian added in conversion, not in the draft: owner to check |
| DoS | yes | keep |  |  |
| oracle manipulation | no | translate | манипуляция оракулом |  |
| rug pull | yes | keep |  |  |
| auditor | no | translate | аудитор |  |
| severity | no | translate | критичность | of a finding |
| PoC | yes | keep |  |  |
| proof of concept | yes | keep |  |  |
| bug bounty | yes | keep |  |  |
| Code4rena | yes | keep |  | heard as: code arena |
| Sherlock | yes | keep |  | audit platform |
| Immunefi | yes | keep |  | heard as: immune fi |
| Cantina | yes | keep |  | audit platform |
