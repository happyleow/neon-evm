import unittest

import solana
from base58 import b58decode
from enum import IntEnum
from solana_utils import *

CONTRACTS_DIR = os.environ.get("CONTRACTS_DIR", "evm_loader/tests")
ETH_TOKEN_MINT_ID: PublicKey = PublicKey(os.environ.get("ETH_TOKEN_MINT"))
evm_loader_id = os.environ.get("EVM_LOADER")
INVALID_NONCE = 'Invalid Ethereum transaction nonce'
INCORRECT_PROGRAM_ID = 'Incorrect Program Id'

NEON_PAYMENT_TO_TREASURE = int(os.environ.get('NEON_PAYMENT_TO_TREASURE', 5000))
NEON_PAYMENT_TO_DEPOSIT = int(os.environ.get('NEON_PAYMENT_TO_DEPOSIT', 5000))


class Step(IntEnum):
    Begin = 0
    Iteration = 1
    Complete = 2


class EvmLoaderTestsNewAccount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print("\ntest_transaction.py setUpClass")

        cls.token = SplToken(solana_url)
        wallet = OperatorAccount(operator1_keypair_path())
        cls.loader = EvmLoader(wallet, evm_loader_id)
        cls.acc = wallet.get_acc()

        # Create ethereum account for user account
        cls.caller_ether = eth_keys.PrivateKey(cls.acc.secret_key()).public_key.to_canonical_address()
        (cls.caller, cls.caller_nonce) = cls.loader.ether2program(cls.caller_ether)
        cls.caller_token = get_associated_token_address(PublicKey(cls.caller), ETH_TOKEN_MINT_ID)

        if getBalance(cls.caller) == 0:
            print("Create caller account...")
            _ = cls.loader.createEtherAccount(cls.caller_ether)
            cls.loader.airdropNeonTokens(cls.caller_ether, 201)
            print("Done\n")

        print('Account:', cls.acc.public_key(), bytes(cls.acc.public_key()).hex())
        print("Caller:", cls.caller_ether.hex(), cls.caller_nonce, "->", cls.caller,
              "({})".format(bytes(PublicKey(cls.caller)).hex()))

        (cls.owner_contract, cls.eth_contract, cls.contract_code) = cls.loader.deployChecked(
            CONTRACTS_DIR + 'helloWorld.binary',
            cls.caller,
            cls.caller_ether
        )

        print("contract id: ", cls.owner_contract, cls.eth_contract)
        print("code id: ", cls.contract_code)

        collateral_pool_index = 2
        cls.collateral_pool_address = create_collateral_pool_address(collateral_pool_index)
        cls.collateral_pool_index_buf = collateral_pool_index.to_bytes(4, 'little')

        wallet_2 = RandomAccount()
        cls.acc_2 = wallet_2.get_acc()
        print("wallet_2: ", wallet_2.path)

        if getBalance(cls.acc_2.public_key()) == 0:
            tx = client.request_airdrop(cls.acc_2.public_key(), 10 * 10 ** 9)
            confirm_transaction(client, tx['result'])

        # Create ethereum account for user 2 account
        cls.caller_ether_2 = eth_keys.PrivateKey(cls.acc_2.secret_key()).public_key.to_canonical_address()
        (cls.caller_2, cls.caller_nonce_2) = cls.loader.ether2program(cls.caller_ether_2)

    def create_storage_account(self, seed):
        storage = PublicKey(
            sha256(bytes(self.acc.public_key()) + bytes(seed, 'utf8') + bytes(PublicKey(evm_loader_id))).digest())
        print("Storage", storage)

        if getBalance(storage) == 0:
            trx = Transaction()
            trx.add(createAccountWithSeed(self.acc.public_key(), self.acc.public_key(), seed, 10 ** 9, 128 * 1024,
                                          PublicKey(evm_loader_id)))
            send_transaction(client, trx, self.acc)

        return storage

    def get_tx(self, nonce):
        return {
            'to': self.eth_contract,
            'value': 0,
            'gas': 9999999999,
            'gasPrice': 1_000_000_000,
            'nonce': nonce,
            'data': '3917b3df',
            'chainId': 111
        }

    def get_keccak_instruction_and_trx_data(self, data_start, secret_key, caller, caller_ether, trx_cnt=None):
        if trx_cnt is None:
            trx_cnt = getTransactionCount(client, caller)
        tx = self.get_tx(trx_cnt)
        (from_addr, sign, msg) = make_instruction_data_from_tx(tx, secret_key)
        keccak_instruction_data = make_keccak_instruction_data(1, len(msg), data_start)
        trx_data = caller_ether + sign + msg

        keccak_instruction = TransactionInstruction(program_id="KeccakSecp256k11111111111111111111111111111",
                                                    data=keccak_instruction_data,
                                                    keys=[AccountMeta(pubkey=caller, is_signer=False, is_writable=False)]
                                                    )
        return keccak_instruction, trx_data, sign

    def get_account_metas_for_instr_05(self, caller):
        return [
            # System instructions account:
            AccountMeta(pubkey=PublicKey(sysinstruct), is_signer=False, is_writable=False),
            # Operator address:
            AccountMeta(pubkey=self.acc.public_key(), is_signer=True, is_writable=True),
            # Collateral pool address:
            AccountMeta(pubkey=self.collateral_pool_address, is_signer=False, is_writable=True),
            # Operator ETH address:
            AccountMeta(pubkey=self.caller, is_signer=False, is_writable=True),
            # System program account:
            AccountMeta(pubkey=PublicKey(system), is_signer=False, is_writable=False),
            # NeonEVM program account
            AccountMeta(pubkey=self.loader.loader_id, is_signer=False, is_writable=False),

            AccountMeta(pubkey=self.owner_contract, is_signer=False, is_writable=True),
            AccountMeta(pubkey=self.contract_code, is_signer=False, is_writable=True),
            AccountMeta(pubkey=caller, is_signer=False, is_writable=True),

            AccountMeta(pubkey=ETH_TOKEN_MINT_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        ]

    def get_account_metas_for_instr_0D(self, storage, caller):
        return [AccountMeta(pubkey=storage, is_signer=False, is_writable=True)] + self.get_account_metas_for_instr_05(caller)

    def neon_emv_instr_05(self, trx_data, caller):
        return TransactionInstruction(
            program_id=self.loader.loader_id,
            data=bytearray.fromhex("05") + self.collateral_pool_index_buf + trx_data,
            keys=self.get_account_metas_for_instr_05(caller)
        )

    def neon_emv_instr_0D(self, step_count, trx_data, storage, caller):
        return TransactionInstruction(
            program_id=self.loader.loader_id,
            data=bytearray.fromhex("0D") + self.collateral_pool_index_buf + step_count.to_bytes(8, byteorder='little') + trx_data,
            keys=self.get_account_metas_for_instr_0D(storage, caller)
        )

    def check_err_is_invalid_nonce(self, err):
        response = json.loads(str(err.result).replace('\'', '\"').replace('None', 'null'))
        print('response:', response)
        print('code:', response['code'])
        self.assertEqual(response['code'], -32002)
        print('INVALID_NONCE:', INVALID_NONCE)
        logs = response['data']['logs']
        print('logs:', logs)
        log = [s for s in logs if INVALID_NONCE in s][0]
        print(log)
        self.assertGreater(len(log), len(INVALID_NONCE))
        file_name = 'src/entrypoint.rs'
        self.assertTrue(file_name in log)

    def check_transfers_between_operator_deposit_and_collateral_pool(
            self, response, operator_sol_acc, deposit_sol_acc, collateral_pool_sol_acc, step=Step.Iteration):
        print('check_transfer_from_operator_to_deposit:')
        print('     response:', response)
        print('     operator_sol_acc:', operator_sol_acc)
        print('     deposit_sol_acc:', deposit_sol_acc)
        print('     collateral_pool_sol_acc:', collateral_pool_sol_acc)
        response = json.loads(str(response).replace('\'', '\"').replace('None', 'null'))
        print('     response:', response)
        account_keys = response['result']['transaction']['message']['accountKeys']
        print('     account_keys:', account_keys)
        operator_sol_acc_index = account_keys.index(str(operator_sol_acc))
        print('     operator_sol_acc_index:', operator_sol_acc_index)
        deposit_sol_acc_index = account_keys.index(str(deposit_sol_acc))
        print('     deposit_sol_acc_index:', deposit_sol_acc_index)
        collateral_pool_sol_acc_index = account_keys.index(str(collateral_pool_sol_acc))
        print('     collateral_pool_sol_acc_index:', collateral_pool_sol_acc_index)
        pre_balances = response['result']['meta']['preBalances']
        print('     pre_balances:', pre_balances)
        post_balances = response['result']['meta']['postBalances']
        print('     post_balances:', post_balances)
        operator_pre_balance = pre_balances[operator_sol_acc_index]
        print('     operator_pre_balance:', operator_pre_balance)
        operator_post_balance = post_balances[operator_sol_acc_index]
        print('     operator_post_balance:', operator_post_balance)
        deposit_pre_balance = pre_balances[deposit_sol_acc_index]
        print('     deposit_pre_balance:', deposit_pre_balance)
        deposit_post_balance = post_balances[deposit_sol_acc_index]
        print('     deposit_post_balance:', deposit_post_balance)
        collateral_pool_pre_balance = pre_balances[collateral_pool_sol_acc_index]
        print('     collateral_pool_pre_balance:', collateral_pool_pre_balance)
        collateral_pool_post_balance = post_balances[collateral_pool_sol_acc_index]
        print('     collateral_pool_post_balance:', collateral_pool_post_balance)
        fee = response['result']['meta']['fee']
        print('     fee:', fee)
        print('     NEON_PAYMENT_TO_DEPOSIT:', NEON_PAYMENT_TO_DEPOSIT)
        print('     NEON_PAYMENT_TO_TREASURE:', NEON_PAYMENT_TO_TREASURE)
        operator_balance_change = int(operator_post_balance) - int(operator_pre_balance)
        print('     operator_balance_change:', operator_balance_change)
        deposit_balance_change = int(deposit_post_balance) - int(deposit_pre_balance)
        print('     deposit_balance_change:', deposit_balance_change)
        collateral_pool_balance_change = int(collateral_pool_post_balance) - int(collateral_pool_pre_balance)
        print('     collateral_pool_balance_change:', collateral_pool_balance_change)
        if step is Step.Begin:
            self.assertEqual(operator_balance_change, 0 - fee - NEON_PAYMENT_TO_DEPOSIT - NEON_PAYMENT_TO_TREASURE)
            self.assertEqual(deposit_balance_change, NEON_PAYMENT_TO_DEPOSIT)
            self.assertEqual(collateral_pool_balance_change, NEON_PAYMENT_TO_TREASURE)
        if step is Step.Iteration:
            self.assertEqual(operator_balance_change, 0 - fee - NEON_PAYMENT_TO_TREASURE)
            self.assertEqual(deposit_balance_change, 0)
            self.assertEqual(collateral_pool_balance_change, NEON_PAYMENT_TO_TREASURE)
        if step is Step.Complete:
            self.assertLessEqual(operator_balance_change, 0 - fee + NEON_PAYMENT_TO_DEPOSIT - NEON_PAYMENT_TO_TREASURE)
            self.assertEqual(deposit_balance_change, 0 - NEON_PAYMENT_TO_DEPOSIT)
            self.assertEqual(collateral_pool_balance_change, NEON_PAYMENT_TO_TREASURE)

    def test_01_success_tx_send(self):
        (keccak_instruction, trx_data, sign) = self.get_keccak_instruction_and_trx_data(5, self.acc.secret_key(), self.caller, self.caller_ether)
        trx = Transaction() \
            .add(keccak_instruction) \
            .add(self.neon_emv_instr_05(trx_data, self.caller))

        response = send_transaction(client, trx, self.acc)
        print('response:', response)

    def test_02_success_tx_send_iteratively_in_3_solana_transactions_sequentially(self):
        step_count = 100
        (keccak_instruction, trx_data, sign) = self.get_keccak_instruction_and_trx_data(13, self.acc.secret_key(), self.caller, self.caller_ether)
        storage = self.create_storage_account(sign[:8].hex())
        neon_emv_instr_0d = self.neon_emv_instr_0D(step_count, trx_data, storage, self.caller)

        trx = Transaction() \
            .add(neon_emv_instr_0d)

        response = send_transaction(client, trx, self.acc)
        print('response_1:', response)
        response = send_transaction(client, trx, self.acc)
        print('response_2:', response)
        response = send_transaction(client, trx, self.acc)
        print('response_3:', response)


        evm_step_executed = 230
        trx_size_cost = 5000
        iterative_overhead = 10_000
        gas = iterative_overhead + trx_size_cost + (evm_step_executed * evm_step_cost())

        self.assertEqual(response['result']['meta']['err'], None)
        data = b58decode(response['result']['meta']['innerInstructions'][-1]['instructions'][-1]['data'])
        self.assertEqual(data[0], 6)  # 6 means OnReturn,
        self.assertLess(data[1], 0xd0)  # less 0xd0 - success
        self.assertEqual(int().from_bytes(data[2:10], 'little'), gas)  # used_gas

    def test_03_failure_tx_send_iteratively_in_4_solana_transactions_sequentially(self):
        step_count = 100
        (keccak_instruction, trx_data, sign) = self.get_keccak_instruction_and_trx_data(13, self.acc.secret_key(), self.caller, self.caller_ether)
        storage = self.create_storage_account(sign[:8].hex())
        neon_emv_instr_0d = self.neon_emv_instr_0D(step_count, trx_data, storage, self.caller)

        trx = Transaction() \
            .add(neon_emv_instr_0d)

        response = send_transaction(client, trx, self.acc)
        print('response_1:', response)
        response = send_transaction(client, trx, self.acc)
        print('response_2:', response)
        response = send_transaction(client, trx, self.acc)
        print('response_3:', response)

        try:
            send_transaction(client, trx, self.acc)
        except Exception as err:
            if str(err).startswith(
                    "Transaction simulation failed: Error processing Instruction 0: custom program error: 0x4"):
                print ("Exception was expected, OK")
                pass
            else:
                raise

    def test_04_success_tx_send_iteratively_by_2_instructions_in_one_transaction(self):
        step_count = 150
        (keccak_instruction, trx_data, sign) = self.get_keccak_instruction_and_trx_data(13, self.acc.secret_key(), self.caller, self.caller_ether)
        storage = self.create_storage_account(sign[:8].hex())
        neon_emv_instr_0d = self.neon_emv_instr_0D(step_count, trx_data, storage, self.caller)

        trx = Transaction() \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d)

        response = send_transaction(client, trx, self.acc)
        print('response:', response)

        evm_step_executed = 230
        trx_size_cost = 5000
        iterative_overhead = 10_000
        gas = iterative_overhead + trx_size_cost + (evm_step_executed * evm_step_cost())

        self.assertEqual(response['result']['meta']['err'], None)
        data = b58decode(response['result']['meta']['innerInstructions'][-1]['instructions'][-1]['data'])
        self.assertEqual(data[0], 6)  # 6 means OnReturn,
        self.assertLess(data[1], 0xd0)  # less 0xd0 - success
        self.assertEqual(int().from_bytes(data[2:10], 'little'), gas)  # used_gas

    def test_05_failure_tx_send_iteratively_by_4_instructions_in_one_transaction(self):
        step_count = 200
        (keccak_instruction, trx_data, sign) = self.get_keccak_instruction_and_trx_data(13, self.acc.secret_key(), self.caller, self.caller_ether)
        storage = self.create_storage_account(sign[:8].hex())
        neon_emv_instr_0d = self.neon_emv_instr_0D(step_count, trx_data, storage, self.caller)

        trx = Transaction() \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d)
            # .add(neon_emv_instr_0d)
        try:
            send_transaction(client, trx, self.acc)
        except Exception as err:
            if str(err).startswith(
                    "Transaction simulation failed: Error processing Instruction 2: custom program error: 0x4"):
                print ("Exception was expected, OK")
                pass
            else:
                raise

    def test_06_failure_tx_send_iteratively_transaction_too_large(self):
        step_count = 100
        (keccak_instruction, trx_data, sign) = self.get_keccak_instruction_and_trx_data(13, self.acc.secret_key(), self.caller, self.caller_ether)
        storage = self.create_storage_account(sign[:8].hex())
        neon_emv_instr_0d = self.neon_emv_instr_0D(step_count, trx_data, storage, self.caller)

        trx = Transaction() \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d) \
            .add(neon_emv_instr_0d)

        with self.assertRaisesRegex(RuntimeError, 'transaction too large'):
            response = send_transaction(client, trx, self.acc)
            print(response)

        print('the solana transaction is too large')

    def test_07_combined_continue_gets_before_the_creation_of_accounts(self):
        evm_steps = 100
        (keccak_instruction, trx_data, sign) = self.get_keccak_instruction_and_trx_data(13, self.acc_2.secret_key(), self.caller_2, self.caller_ether_2, 0)
        storage = self.create_storage_account(sign[:8].hex())
        neon_emv_instr_0d_2 = self.neon_emv_instr_0D(evm_steps, trx_data, storage, self.caller_2)
        print('neon_emv_instr_0d_2: ', neon_emv_instr_0d_2)

        trx = Transaction() \
            .add(neon_emv_instr_0d_2)

        print('Send a transaction "combined continue(0x0d)" before creating an account - wait for the confirmation '
              'and make sure of the error. See https://github.com/neonlabsorg/neon-evm/pull/320')
        with self.assertRaisesRegex(Exception, "invalid program argument"):
            send_transaction(client, trx, self.acc)

        if getBalance(self.caller_2) == 0:
            print("Send a transaction to create an account - wait for the confirmation and make sure of successful "
                  "completion")
            _ = self.loader.createEtherAccount(self.caller_ether_2)
            print('Transfer tokens to the user token account')
            self.loader.airdropNeonTokens(self.caller_ether_2, 100)
            print("Done\n")

        print('Account_2:', self.acc_2.public_key(), bytes(self.acc_2.public_key()).hex())
        print("Caller_2:", self.caller_ether_2.hex(), self.caller_nonce_2, "->", self.caller_2,
              "({})".format(bytes(PublicKey(self.caller_2)).hex()))
        neon_balance_on_start = getNeonBalance(client, self.caller_2)
        print("Caller_2 NEON-token balance:", neon_balance_on_start)

        print('Send several transactions "combined continue(0x0d)" - wait for the confirmation and make sure of a '
              'successful completion')

        response_0 = send_transaction(client, trx, self.acc)
        print('response_0:', response_0)
        neon_balance_on_response_0 = getNeonBalance(client, self.caller_2)
        print("Caller_2 NEON-token balance on response_0:", neon_balance_on_response_0)
        response_1 = send_transaction(client, trx, self.acc)
        print('response_1:', response_1)
        neon_balance_on_response_1 = getNeonBalance(client, self.caller_2)
        print("Caller_2 NEON-token balance on response_1:", neon_balance_on_response_1)

        response_2 = send_transaction(client, trx, self.acc)
        print('response_2:', response_2)
        neon_balance_on_response_2 = getNeonBalance(client, self.caller_2)
        print("Caller_2 NEON-token balance on response_2:", neon_balance_on_response_2)

        evm_step_executed = 230
        trx_size_cost = 5000
        iterative_overhead = 10_000
        gas1 = iterative_overhead + trx_size_cost + (evm_steps * evm_step_cost())
        gas2 = evm_steps * evm_step_cost()
        gas3 = (evm_step_executed - evm_steps - evm_steps) * evm_step_cost()
        gas = gas1 + gas2 + gas3


        self.assertEqual(response_2['result']['meta']['err'], None)
        data = b58decode(response_2['result']['meta']['innerInstructions'][-1]['instructions'][-1]['data'])
        self.assertEqual(data[0], 6)  # 6 means OnReturn,
        self.assertLess(data[1], 0xd0)  # less 0xd0 - success
        self.assertEqual(int().from_bytes(data[2:10], 'little'), gas)  # used_gas
        print('the ether transaction was completed after creating solana-eth-account by three 0x0d transactions')

        try:
            send_transaction(client, trx, self.acc)
        except Exception as err:
            if str(err).startswith(
                    "Transaction simulation failed: Error processing Instruction 0: custom program error: 0x4"):
                print("Exception was expected, OK")
                pass
            else:
                raise
        neon_balance_on_5_th_transaction = getNeonBalance(client, self.caller_2)

        print("neon_balance_on_response_1", neon_balance_on_response_0)
        print("neon_balance_on_response_2", neon_balance_on_response_1)
        print("neon_balance_on_response_3", neon_balance_on_response_2)
        print('Caller_2 NEON-token balance on sending 5-th transaction:', neon_balance_on_5_th_transaction)

        gas_price = 10**9
        self.assertEqual(neon_balance_on_start - neon_balance_on_response_0 , gas1 * gas_price)
        self.assertEqual(neon_balance_on_start - neon_balance_on_response_1, (gas1 + gas2) * gas_price)
        self.assertEqual(neon_balance_on_start - neon_balance_on_response_2, (gas1 + gas2 + gas3) * gas_price)
        self.assertEqual(neon_balance_on_response_2 - neon_balance_on_5_th_transaction, 0)

        print('Check Transfer to treasures on each iteration #345.')
        print('See https://github.com/neonlabsorg/neon-evm/issues/345:')
        operator_sol_acc = self.acc.public_key()
        collateral_pool_sol_acc = self.collateral_pool_address
        deposit_sol_acc = storage

        self.check_transfers_between_operator_deposit_and_collateral_pool(response_0, operator_sol_acc,
                                                                          deposit_sol_acc, collateral_pool_sol_acc,
                                                                          step=Step.Begin)
        self.check_transfers_between_operator_deposit_and_collateral_pool(response_1, operator_sol_acc,
                                                                          deposit_sol_acc, collateral_pool_sol_acc,
                                                                          step=Step.Iteration)
        self.check_transfers_between_operator_deposit_and_collateral_pool(response_2, operator_sol_acc,
                                                                          deposit_sol_acc, collateral_pool_sol_acc,
                                                                          step=Step.Complete)


if __name__ == '__main__':
    unittest.main()