use evm::backend::Apply;
use primitive_types::{H160, H256, U256};
use solana_client::rpc_client::RpcClient;
use solana_sdk::{
    pubkey::Pubkey,
    account::Account,
};
use serde_json::json;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use evm_loader::{
    solana_backend::AccountStorage,
    solidity_account::SolidityAccount,
};
use std::borrow::BorrowMut;
use std::cell::RefCell; 
use std::rc::Rc;

#[derive(Serialize, Deserialize, Debug)]
struct AccountJSON {
    address: String,
    writable: bool,
    new: bool,
}

struct SolanaAccount {
    account: Account,
    key: Pubkey,
    writable: bool,
}

impl SolanaAccount {
    pub fn new(account: Account, key: Pubkey,) -> SolanaAccount {
        eprintln!("SolanaAccount::new");
        Self{account, key, writable: false}
    }
}

pub struct EmulatorAccountStorage {
    accounts: RefCell<HashMap<H160, SolanaAccount>>,
    new_accounts: RefCell<HashSet<H160>>,
    rpc_client: RpcClient,
    program_id: Pubkey,
    contract_id: H160,
    caller_id: H160,
    base_account: Pubkey,
    block_number: u64,
    block_timestamp: i64,
}

impl EmulatorAccountStorage {
    pub fn new(solana_url: String, base_account: Pubkey, program_id: Pubkey, contract_id: H160, caller_id: H160) -> EmulatorAccountStorage {
        eprintln!("backend::new");

        let rpc_client = RpcClient::new(solana_url);

        let slot = match rpc_client.get_slot() {
            Ok(slot) => {
                eprintln!("Got slot");
                eprintln!("Slot {}", slot);
                slot
            },
            Err(_) => {
                eprintln!("Get slot error");
                0
            }
        };
    
        let timestamp = match rpc_client.get_block_time(slot) {
            Ok(timestamp) => {
                eprintln!("Got timestamp");
                eprintln!("timestamp {}", timestamp);
                timestamp
            },
            Err(_) => {
                eprintln!("Get timestamp error");
                0
            }
        };

        Self {
            accounts: RefCell::new(HashMap::new()),
            new_accounts: RefCell::new(HashSet::new()),
            rpc_client: rpc_client,
            program_id: program_id,
            contract_id: contract_id,
            caller_id: caller_id,
            base_account: base_account,
            block_number: slot,
            block_timestamp: timestamp,
        }
    }

    fn create_acc_if_not_exists(&self, address: &H160) -> bool {
        let mut accounts = self.accounts.borrow_mut(); 
        let mut new_accounts = self.new_accounts.borrow_mut(); 
        if accounts.get(address).is_none() {
            let solana_address = if *address == self.contract_id {
                Pubkey::find_program_address(&[&address.to_fixed_bytes()], &self.program_id).0
            } else {
                let seed = bs58::encode(&address.to_fixed_bytes()).into_string();
                Pubkey::create_with_seed(&self.base_account, &seed, &self.program_id).unwrap()
            };

            eprintln!("Not found account for {} => {}", &address.to_string(), &solana_address.to_string());
            
            match self.rpc_client.get_account(&solana_address) {
                Ok(acc) => {
                    eprintln!("Account found");                        
                    eprintln!("Account data len {}", acc.data.len());
                    eprintln!("Account owner {}", acc.owner.to_string());

                    accounts.insert(address.clone(), SolanaAccount::new(acc, solana_address));

                    true
                },
                Err(_) => {
                    eprintln!("Account not found {}", &address.to_string());

                    new_accounts.insert(address.clone());

                    false
                }
            }
        } else {
            true
        }
    }

    // pub fn make_solidity_account<'a>(self, account:&'a SolanaAccount) -> SolidityAccount<'a> {
    //     let mut data = account.account.data.clone();
    //     let data_rc: std::rc::Rc<std::cell::RefCell<&mut [u8]>> = Rc::new(RefCell::new(&mut data));
    //     SolidityAccount::new(&account.key, data_rc, account.account.lamports).unwrap()
    // }

    pub fn apply<A, I>(&self, values: A)
            where
                A: IntoIterator<Item=Apply<I>>,
                I: IntoIterator<Item=(H256, H256)>,
    {             
        let mut accounts = self.accounts.borrow_mut(); 

        for apply in values {
            match apply {
                Apply::Modify {address, basic, code: _, storage: _, reset_storage} => {
                    match accounts.get_mut(&address) {
                        Some(acc) => {
                            *acc.writable.borrow_mut() = true;
                        },
                        None => {
                            eprintln!("Account not found {}", &address.to_string());
                        },
                    }
                    eprintln!("Modify: {} {} {} {}", &address.to_string(), &basic.nonce.as_u64(), &basic.balance.as_u64(), &reset_storage.to_string());
                },
                Apply::Delete {address: addr} => {
                    eprintln!("Delete: {}", addr.to_string());
                },
            }
        };
    }

    pub fn get_used_accounts(&self, status: &String, result: &std::vec::Vec<u8>)
    {          
        let mut arr = Vec::new();    
        
        eprint!("[");
        let accounts = self.accounts.borrow();
        for (address, acc) in accounts.iter() {
            arr.push(AccountJSON{address: "0x".to_string() + &hex::encode(&address.to_fixed_bytes()), writable: acc.writable, new: false});
            eprint!("{{\"address\":\"0x{}\",\"write\":\"{}\"}},", &hex::encode(&address.to_fixed_bytes()), &acc.writable.to_string());
        }
        let new_accounts = self.new_accounts.borrow(); 
        for address in new_accounts.iter() {
            arr.push(AccountJSON{address: "0x".to_string() + &hex::encode(&address.to_fixed_bytes()), writable: false, new: true});
            eprint!("{{\"address\":\"0x{}\",\"new\":\"true\"}},", &hex::encode(&address.to_fixed_bytes()));
        }    
        eprintln!("]");

        let js = json!({"accounts": arr, "result": &hex::encode(&result), "exit_status": &status}).to_string();

        println!("{}", js);
    }
}

impl AccountStorage for EmulatorAccountStorage {
    fn apply_to_account<U, D, F>(&self, address: &H160, d: D, f: F) -> U
    where F: FnOnce(&SolidityAccount) -> U,
          D: FnOnce() -> U
    {
        self.create_acc_if_not_exists(address);
        let accounts = self.accounts.borrow();
        match accounts.get(&address) {
            None => d(),
            Some(acc) => {
                let mut account_data = acc.account.data.clone();
                let account = SolidityAccount::new(&acc.key, Rc::new(RefCell::new(&mut account_data)), acc.account.lamports).unwrap();
                f(&account)
            },
        }
    }

    fn apply_to_contract<U, D, F>(&self, d: D, f: F) -> U
    where F: FnOnce(&SolidityAccount) -> U,
          D: FnOnce() -> U
    {
        self.apply_to_account(&self.contract_id, d, f)
    }
        
    fn apply_to_caller<U, D, F>(&self, d: D, f: F) -> U
    where F: FnOnce(&SolidityAccount) -> U,
          D: FnOnce() -> U
    {
        self.apply_to_account(&self.caller_id, d, f)
    }

    fn origin(&self) -> H160 { self.contract_id }

    fn block_number(&self) -> U256 { self.block_number.into() }

    fn block_timestamp(&self) -> U256 { self.block_timestamp.into() }
}